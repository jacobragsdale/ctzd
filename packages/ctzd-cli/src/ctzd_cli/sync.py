"""Platform-owned files in an app: write them and detect changes to them.

Build files (the locked files, the managed tables of pyproject.toml, and, in a
React app, the firm's npm packages in web/package.json and pnpm's overrides)
are committed and pinned to the platform release in `.ctzd/version`; every
value comes from that release. Guidance
(`AGENTS.md` and the skills) is gitignored and rewritten from the channel's
current release; `.ctzd/guidance.json` records when that last happened.
"""

import json
import re
import shutil
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import tomlkit
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from tomlkit.items import Table

from ctzd_cli import api_models as m
from ctzd_cli import assets
from ctzd_cli.appconfig import AppConfig, Ui
from ctzd_cli.config import PlatformConfig

VERSION_FILE = ".ctzd/version"
GUIDANCE_STAMP = ".ctzd/guidance.json"
ENV_EXAMPLE = ".env.example"
# How long guidance counts as fresh before a command asks the platform for the current release again.
GUIDANCE_REFRESH = timedelta(minutes=5)
_GUIDANCE_DIRS = (assets.SKILLS_DIR, assets.CLAUDE_SKILLS_DIR)


@dataclass(frozen=True)
class Drift:
    path: str
    problem: Literal["changed", "missing"]


def expected_files(release: m.PlatformRelease, ui: Ui) -> dict[str, str]:
    return assets.locked_files(release, ui) | {VERSION_FILE: f"{release.release}\n"}


# The tables of pyproject.toml the platform owns, as dotted keys.
MANAGED_PYPROJECT_KEYS = ("project.requires-python", "build-system", "dependency-groups.dev", "tool.uv")
PACKAGE_JSON = "web/package.json"
PNPM_WORKSPACE = "web/pnpm-workspace.yaml"
PNPM_LOCK = "web/pnpm-lock.yaml"
WEB_FILES = (PACKAGE_JSON, PNPM_WORKSPACE, PNPM_LOCK)


def python_index(platform: PlatformConfig, release: m.PlatformRelease) -> tuple[m.PackageIndex, ...]:
    overrides = platform.release_overrides
    return release.python.index if overrides is None or overrides.python_index is None else overrides.python_index


def npm_pins(platform: PlatformConfig, release: m.PlatformRelease) -> dict[str, str]:
    """The firm's npm packages and the spec each is pinned to, as pnpm reads it."""
    overrides = platform.release_overrides
    packages = release.npm.packages if overrides is None or overrides.npm_packages is None else release.npm.packages | overrides.npm_packages
    return {name: m.expand_spec(spec) for name, spec in sorted(packages.items())}


def managed_pyproject(platform: PlatformConfig, release: m.PlatformRelease) -> dict[str, object]:
    """The parts of pyproject.toml the platform owns, as dotted keys and their values."""
    uv: dict[str, object] = {
        "build-backend": {"module-name": "app"},
        "index": [index.model_dump(exclude_none=True, exclude_defaults=True) | {"name": index.name, "url": index.url} for index in python_index(platform, release)],
    }
    if release.python.sources:
        uv["sources"] = dict(release.python.sources)
    values: dict[str, object] = {
        "project.requires-python": release.python.requires,
        "build-system": {"requires": [release.python.build_backend], "build-backend": "uv_build"},
        "dependency-groups.dev": list(release.python.dev),
        "tool.uv": uv,
    }
    return {key: values[key] for key in MANAGED_PYPROJECT_KEYS}


def same_build(platform: PlatformConfig, one: m.PlatformRelease, other: m.PlatformRelease, ui: Ui) -> bool:
    """Whether two releases would write the same build files for an app of kind `ui`; the recorded number aside."""
    same_web = ui != "react" or npm_pins(platform, one) == npm_pins(platform, other)
    return assets.locked_files(one, ui) == assets.locked_files(other, ui) and managed_pyproject(platform, one) == managed_pyproject(platform, other) and same_web


_OBJECT: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def _package_json(root: Path) -> dict[str, object] | None:
    path = root / PACKAGE_JSON
    if not path.is_file():
        return None
    try:
        return _OBJECT.validate_json(path.read_bytes())
    except ValidationError:
        return None


def _override_line(package: str) -> re.Pattern[str]:
    """A line of pnpm-workspace.yaml's overrides that pins `package`."""
    return re.compile(rf"""^(?P<key>\s+["']?{re.escape(package)}["']?:\s*)(?P<value>\S.*?)\s*$""", re.MULTILINE)


def web_drift(root: Path, platform: PlatformConfig, release: m.PlatformRelease) -> list[Drift]:
    """The firm's npm packages in web/package.json and pnpm's overrides, against the release's pins."""
    problems: list[Drift] = []
    document = _package_json(root)
    dependencies = _OBJECT.validate_python(document.get("dependencies", {})) if document is not None and isinstance(document.get("dependencies"), dict) else {}
    workspace = (root / PNPM_WORKSPACE).read_text(encoding="utf-8") if (root / PNPM_WORKSPACE).is_file() else ""
    for package, spec in npm_pins(platform, release).items():
        if document is None or dependencies.get(package) != spec:
            problems.append(Drift(f"{PACKAGE_JSON} [dependencies.{package}]", "changed"))
        stale = [found for found in _override_line(package).finditer(workspace) if found.group("value").strip("\"'") != spec]
        problems += [Drift(f"{PNPM_WORKSPACE} [overrides.{package}]", "changed")] if stale else []
    return problems


def write_web(root: Path, platform: PlatformConfig, release: m.PlatformRelease) -> list[str]:
    """Pin the firm's npm packages to `release`'s in web/package.json and pnpm's overrides; return the files changed."""
    pins = npm_pins(platform, release)
    changed: list[str] = []
    document = _package_json(root)
    if document is not None:
        dependencies = _OBJECT.validate_python(document.get("dependencies", {})) if isinstance(document.get("dependencies"), dict) else {}
        updated = {**document, "dependencies": dependencies | pins}
        if updated != document:
            (root / PACKAGE_JSON).write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            changed.append(PACKAGE_JSON)
    workspace = root / PNPM_WORKSPACE
    if workspace.is_file():
        before = workspace.read_text(encoding="utf-8")
        after = before
        for package, spec in pins.items():
            after = _override_line(package).sub(lambda found, spec=spec: f'{found.group("key")}"{spec}"', after)
        if after != before:
            workspace.write_text(after, encoding="utf-8")
            changed.append(PNPM_WORKSPACE)
    return changed


def outside_managed_web(path: str, text: str | None, platform: PlatformConfig, release: m.PlatformRelease) -> object:
    """A web file without the values the platform manages: what the person may change. None when unreadable."""
    if text is None:
        return None
    packages = set(npm_pins(platform, release))
    if path == PACKAGE_JSON:
        try:
            document = _OBJECT.validate_json(text)
        except ValidationError:
            return None
        dependencies = document.get("dependencies")
        if isinstance(dependencies, dict):
            document["dependencies"] = {key: value for key, value in dependencies.items() if key not in packages}
        return document
    for package in packages:
        text = _override_line(package).sub(lambda found: found.group("key"), text)
    return text


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n")


_TABLE: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def _lookup(document: dict[str, object], dotted: str) -> object:
    node: object = document
    for part in dotted.split("."):
        try:
            table = _TABLE.validate_python(node)
        except ValidationError:
            return None
        if part not in table:
            return None
        node = table[part]
    return node


def drift(root: Path, platform: PlatformConfig, release: m.PlatformRelease, ui: Ui) -> list[Drift]:
    problems: list[Drift] = []
    for path, text in sorted(expected_files(release, ui).items()):
        target = root / path
        if not target.is_file():
            problems.append(Drift(path, "missing"))
        elif _normalized(target.read_text(encoding="utf-8")) != text:
            problems.append(Drift(path, "changed"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    for key, value in managed_pyproject(platform, release).items():
        if _lookup(pyproject, key) != value:
            problems.append(Drift(f"pyproject.toml [{key}]", "changed"))
    if ui == "react":
        problems += web_drift(root, platform, release)
    return problems


def _set_dotted(document: tomlkit.TOMLDocument, dotted: str, value: object) -> None:
    *parents, last = dotted.split(".")
    node: tomlkit.TOMLDocument | Table = document
    for part in parents:
        child = node.get(part)
        if not isinstance(child, Table):
            child = tomlkit.table()
            node[part] = child
        node = child
    node[last] = value


def write_pyproject(root: Path, platform: PlatformConfig, release: m.PlatformRelease) -> bool:
    path = root / "pyproject.toml"
    before = path.read_text(encoding="utf-8")
    document = tomlkit.parse(before)
    for key, value in managed_pyproject(platform, release).items():
        _set_dotted(document, key, value)
    after = tomlkit.dumps(document)
    if after != before:
        path.write_text(after, encoding="utf-8")
    return after != before


def write_build(root: Path, platform: PlatformConfig, release: m.PlatformRelease, ui: Ui) -> list[str]:
    """Bring every build file of an app of kind `ui` to `release` and record it; return the paths that changed."""
    changed: list[str] = []
    for path, text in expected_files(release, ui).items():
        target = root / path
        if target.is_file() and _normalized(target.read_text(encoding="utf-8")) == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
        changed.append(path)
    if write_pyproject(root, platform, release):
        changed.append("pyproject.toml")
    if ui == "react":
        changed += write_web(root, platform, release)
    return sorted(changed)


def env_example(app: AppConfig) -> str:
    lines = [
        "# Written by ctzd sync. `ctzd env` fills a real .env with these variables; never commit .env.",
        *(f"CTZD_SOURCE_{name.upper()}=" for name in app.sources.use),
        "CTZD_SHARED_STORE_SAS=",
        "",
    ]
    return "\n".join(lines)


def write_env_example(root: Path, app: AppConfig) -> bool:
    example = root / ENV_EXAMPLE
    if example.is_file() and example.read_text(encoding="utf-8") == env_example(app):
        return False
    example.write_text(env_example(app), encoding="utf-8")
    return True


def write_guidance(root: Path, release: m.PlatformRelease, sbu: str) -> bool:
    """Make `AGENTS.md` and both skill folders exactly `release`'s; return whether anything changed."""
    expected = assets.guidance_files(release, sbu)
    present = {file.relative_to(root).as_posix() for folder in _GUIDANCE_DIRS if (root / folder).is_dir() for file in (root / folder).rglob("*") if file.is_file()}
    extra = present - expected.keys()
    stale = [path for path, text in expected.items() if not (root / path).is_file() or (root / path).read_text(encoding="utf-8") != text]
    if not extra and not stale:
        return False
    for folder in _GUIDANCE_DIRS:
        if (root / folder).exists():
            shutil.rmtree(root / folder)
    for path, text in expected.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
    return True


class GuidanceStamp(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)
    release: int
    checked_at: datetime


def read_stamp(root: Path) -> GuidanceStamp | None:
    path = root / GUIDANCE_STAMP
    if not path.is_file():
        return None
    try:
        return GuidanceStamp.model_validate_json(path.read_bytes())
    except ValidationError:
        return None


def write_stamp(root: Path, release: int, checked_at: datetime) -> None:
    path = root / GUIDANCE_STAMP
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(GuidanceStamp(release=release, checked_at=checked_at).model_dump_json(), encoding="utf-8")


def recorded_release(root: Path) -> int | None:
    """The platform release the app's build files came from; None when nothing usable is recorded."""
    path = root / VERSION_FILE
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None
