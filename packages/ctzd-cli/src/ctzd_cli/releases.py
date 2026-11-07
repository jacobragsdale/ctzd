"""Platform releases: where commands get them, and the checks every release must pass before it is used.

A release comes from the platform API when it is reachable. Releases never
change once published, so every one fetched is kept under the ctzd home
folder. Without the platform (no login, no network, `--offline`) commands use
that cache, or the seed release bundled with this CLI.
"""

import contextlib
import tomllib
from collections.abc import Callable, Mapping
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from packaging.version import Version
from pydantic import TypeAdapter, ValidationError

from ctzd_cli import api_models as m
from ctzd_cli import assets, config
from ctzd_cli.api_client import Platform
from ctzd_cli.errors import CliError, PlatformUnavailableError

# The newest release layout this CLI understands; see assets/release.toml.
FORMAT = 1
TAG_PREFIX = "platform-release/"
MANIFEST = "release.toml"
_TABLE: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def from_files(files: Mapping[str, str]) -> m.PlatformRelease:
    """A release from a folder laid out like `ctzd_cli/assets`, as relative path -> text."""
    manifest_text = files.get(MANIFEST)
    if manifest_text is None:
        msg = f"The platform release has no {MANIFEST}."
        raise CliError(msg)
    try:
        parsed: object = tomllib.loads(manifest_text)
        manifest = _TABLE.validate_python(parsed)
        content = {path: text for path, text in files.items() if path.startswith(m.RELEASE_FOLDERS)}
        return m.PlatformRelease.model_validate({**manifest, "files": content})
    except (tomllib.TOMLDecodeError, ValidationError) as err:
        msg = f"The platform release is invalid: {err}"
        raise CliError(msg) from None


def _tree(folder: Traversable, prefix: str = "") -> dict[str, str]:
    files: dict[str, str] = {}
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        path = f"{prefix}{entry.name}"
        if entry.is_dir():
            if entry.name != "__pycache__":
                files |= _tree(entry, f"{path}/")
        else:
            files[path] = entry.read_text(encoding="utf-8")
    return files


def seed_files() -> dict[str, str]:
    return _tree(resources.files("ctzd_cli").joinpath("assets"))


def seed() -> m.PlatformRelease:
    """The release bundled with this CLI, for working without the platform."""
    return from_files(seed_files())


def usable(release: m.PlatformRelease) -> bool:
    return release.format <= FORMAT and Version(release.requires_cli) <= Version(assets.version())


def require_usable(release: m.PlatformRelease) -> m.PlatformRelease:
    """Stop when this CLI is too old for `release`; the agent upgrades it and runs the command again."""
    installed = assets.version()
    if not usable(release):
        msg = f"Platform release {release.release} needs ctzd {release.requires_cli} or newer; this is ctzd {installed}."
        raise CliError(msg, hint="Run `uv tool upgrade ctzd`, then run the same ctzd command again.")
    return release


def upgrade_hint(current: m.CurrentRelease) -> str | None:
    if current.cli is None or Version(current.cli) <= Version(assets.version()):
        return None
    return f"ctzd {current.cli} is available (this is {assets.version()}); run `uv tool upgrade ctzd` when convenient."


def cache_dir() -> Path:
    return config.user_dir() / "releases"


class ReleaseSource:
    """Platform releases for one ctzd command, from the API when it answers and from local copies when it does not."""

    def __init__(self, platform_factory: Callable[[], Platform] | None) -> None:
        self._factory = platform_factory
        self._platform: Platform | None = None
        self._unreachable: str | None = None
        self._current: dict[tuple[str | None, str | None], m.CurrentRelease | None] = {}
        self._seed: m.PlatformRelease | None = None

    @property
    def unreachable(self) -> str | None:
        """Why a call to the platform failed; None while it answers, and when working offline on purpose."""
        return self._unreachable

    def _client(self) -> Platform | None:
        if self._unreachable is not None or self._factory is None:
            return None
        if self._platform is None:
            try:
                self._platform = self._factory()
            except CliError as err:
                self._unreachable = err.message
                return None
        return self._platform

    def current(self, sbu: str | None = None, app: str | None = None) -> m.CurrentRelease | None:
        """The release this app's channel runs (the general channel's without an app), or None when the platform cannot be asked."""
        key = (sbu, app)
        if key not in self._current:
            client = self._client()
            found = None
            if client is not None:
                try:
                    found = client.current_release(sbu, app)
                except CliError as err:
                    self._unreachable = err.message
            self._current[key] = found
        return self._current[key]

    def _seed_release(self) -> m.PlatformRelease:
        if self._seed is None:
            self._seed = seed()
        return self._seed

    def get(self, number: int) -> m.PlatformRelease:
        """Release `number`: cached, else from the platform, else the seed when it is that release."""
        cached = _read_cache(number)
        if cached is not None:
            return cached
        client = self._client()
        refused: CliError | None = None
        if client is not None:
            try:
                fetched = client.release(number)
            except PlatformUnavailableError as err:
                self._unreachable = err.message
            except CliError as err:
                refused = err
            else:
                if fetched.release != number:
                    msg = f"The platform sent release {fetched.release} when asked for {number}."
                    raise CliError(msg)
                _write_cache(fetched)
                return fetched
        local = self._seed_release()
        if local.release == number:
            return local
        if refused is not None:
            raise refused
        msg = f"Platform release {number} is not on this computer and the platform could not be asked ({self._unreachable or 'working offline'})."
        raise CliError(msg, hint="Connect to the company network, check `ctzd setup`, and run the command again.")

    def newest_local(self) -> m.PlatformRelease:
        """The newest release this CLI can use that is available without the platform."""
        candidates = [self._seed_release()]
        if cache_dir().is_dir():
            for path in cache_dir().glob("*.json"):
                if path.stem.isdigit():
                    cached = _read_cache(int(path.stem))
                    if cached is not None and usable(cached):
                        candidates.append(cached)
        return max(candidates, key=lambda release: release.release)

    def for_app(self, sbu: str | None = None, app: str | None = None) -> m.PlatformRelease:
        """The channel's current release, or the newest local one without the platform."""
        current = self.current(sbu, app)
        return self.newest_local() if current is None else self.get(current.release)


def _cache_path(number: int) -> Path:
    return cache_dir() / f"{number}.json"


def _read_cache(number: int) -> m.PlatformRelease | None:
    path = _cache_path(number)
    if not path.is_file():
        return None
    try:
        cached = m.PlatformRelease.model_validate_json(path.read_bytes())
    except ValidationError:
        # A damaged copy is fetched again; releases never change, so nothing is lost.
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    return cached if cached.release == number else None


def _write_cache(release: m.PlatformRelease) -> None:
    path = _cache_path(release.release)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(release.model_dump_json(), encoding="utf-8")
    temporary.replace(path)
