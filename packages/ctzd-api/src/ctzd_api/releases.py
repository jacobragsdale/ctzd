"""Platform releases as the API serves them: read from the platform repo, checked, and cached."""

import io
import tarfile
import threading
import time
import tomllib
from collections.abc import Callable
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ctzd_api.gateways import Releases
from ctzd_cli import api_models as m
from ctzd_cli import releases
from ctzd_cli.errors import CliError

# Where the release folder sits in the platform repo.
RELEASES_PATH = "packages/ctzd-cli/src/ctzd_cli/assets"
CHANNELS_FILE = "channels.toml"
# Moving a channel pointer is a commit; the API notices within this many seconds.
CHANNELS_TTL = 60.0


class BrokenReleaseError(Exception):
    """A release tag or channels.toml that cannot be served as it is."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class Pointers(_Model):
    pilot: int = Field(ge=1)
    general: int = Field(ge=1)


class Pilot(_Model):
    apps: frozenset[str] = frozenset()


class Channels(_Model):
    """channels.toml: which release each channel runs, which apps are on pilot, and the newest ctzd."""

    cli: str | None = None
    channels: Pointers
    pilot: Pilot = Pilot()

    def current(self, repo: str | None) -> m.CurrentRelease:
        """The release `repo`'s channel runs; without a repo, the general channel's."""
        channel: m.Channel = "pilot" if repo is not None and repo in self.pilot.apps else "general"
        number = self.channels.pilot if channel == "pilot" else self.channels.general
        return m.CurrentRelease(release=number, channel=channel, cli=self.cli)


def parse_channels(text: str) -> Channels:
    try:
        parsed: object = tomllib.loads(text)
        return Channels.model_validate(parsed)
    except (tomllib.TOMLDecodeError, ValidationError) as err:
        msg = f"{CHANNELS_FILE} is invalid: {err}"
        raise BrokenReleaseError(msg) from None


def files_in_archive(archive: bytes, path: str = RELEASES_PATH) -> dict[str, str]:
    """The text files under `path` in a repository tarball, keyed relative to `path`.

    GitHub's tarballs put everything under one top folder named after the
    commit, so the first path component is dropped.
    """
    prefix = PurePosixPath(path)
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts[1:]
            relative = PurePosixPath(*parts) if parts else None
            if relative is None or ".." in parts or not relative.is_relative_to(prefix) or "__pycache__" in parts:
                continue
            extracted = tar.extractfile(member)
            if extracted is not None:
                files[relative.relative_to(prefix).as_posix()] = extracted.read().decode()
    return files


class ReleaseStore:
    """Releases never change, so each is read once; channels.toml is read again after `ttl` seconds."""

    def __init__(self, gateway: Releases, *, clock: Callable[[], float] = time.monotonic, ttl: float = CHANNELS_TTL) -> None:
        self._gateway = gateway
        self._clock = clock
        self._ttl = ttl
        self._lock = threading.Lock()
        self._channels: tuple[Channels, float] | None = None
        self._releases: dict[int, m.PlatformRelease] = {}

    def channels(self) -> Channels:
        now = self._clock()
        with self._lock:
            if self._channels is not None and self._channels[1] > now:
                return self._channels[0]
        parsed = parse_channels(self._gateway.channels_text())
        with self._lock:
            self._channels = (parsed, now + self._ttl)
        return parsed

    def release(self, number: int) -> m.PlatformRelease | None:
        with self._lock:
            if number in self._releases:
                return self._releases[number]
        archive = self._gateway.release_archive(number)
        if archive is None:
            return None
        try:
            found = releases.from_files(files_in_archive(archive))
        except (CliError, tarfile.TarError, UnicodeDecodeError) as err:
            msg = f"Tag {releases.TAG_PREFIX}{number} is not a valid platform release: {err}"
            raise BrokenReleaseError(msg) from None
        if found.release != number:
            msg = f"Tag {releases.TAG_PREFIX}{number} holds a release.toml that says release {found.release}."
            raise BrokenReleaseError(msg)
        with self._lock:
            self._releases[number] = found
        return found
