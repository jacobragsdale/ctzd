"""Platform-owned files in an app: write them and detect changes to them.

Build files (the locked files and the managed tables of pyproject.toml) are
committed and pinned to the platform release in `.ctzd/version`. Guidance
(`AGENTS.md` and the skills) is gitignored and rewritten from the channel's
current release; `.ctzd/guidance.json` records when that last happened.
"""

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
from ctzd_cli.appconfig import AppConfig
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


def expected_files(release: m.PlatformRelease) -> dict[str, str]:
    return assets.locked_files(release) | {VERSION_FILE: f"{release.release}\n"}


# The tables of pyproject.toml the platform owns, as dotted keys.
MANAGED_PYPROJECT_KEYS = ("project.requires-python", "build-system", "dependency-groups.dev", "tool.uv")


def managed_pyproject(platform: PlatformConfig, release: m.PlatformRelease) -> dict[str, object]:
    """The parts of pyproject.toml the platform owns, as dotted keys and their values."""
    uv: dict[str, object] = {
        "build-backend": {"module-name": "app"},
        "index": [index.model_dump(exclude_none=True, exclude_defaults=True) | {"name": index.name, "url": index.url} for index in platform.python.index],
    }
    if platform.python.sources:
        uv["sources"] = dict(platform.python.sources)
    values: dict[str, object] = {
        "project.requires-python": release.python.requires,
        "build-system": {"requires": [release.python.build_backend], "build-backend": "uv_build"},
        "dependency-groups.dev": list(release.python.dev),
        "tool.uv": uv,
    }
    return {key: values[key] for key in MANAGED_PYPROJECT_KEYS}


def same_build(platform: PlatformConfig, one: m.PlatformRelease, other: m.PlatformRelease) -> bool:
    """Whether two releases would write the same build files; the recorded number aside."""
    return assets.locked_files(one) == assets.locked_files(other) and managed_pyproject(platform, one) == managed_pyproject(platform, other)


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


def drift(root: Path, platform: PlatformConfig, release: m.PlatformRelease) -> list[Drift]:
    problems: list[Drift] = []
    for path, text in sorted(expected_files(release).items()):
        target = root / path
        if not target.is_file():
            problems.append(Drift(path, "missing"))
        elif _normalized(target.read_text(encoding="utf-8")) != text:
            problems.append(Drift(path, "changed"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    for key, value in managed_pyproject(platform, release).items():
        if _lookup(pyproject, key) != value:
            problems.append(Drift(f"pyproject.toml [{key}]", "changed"))
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


def write_build(root: Path, platform: PlatformConfig, release: m.PlatformRelease) -> list[str]:
    """Bring every build file to `release` and record it; return the paths that changed."""
    changed: list[str] = []
    for path, text in expected_files(release).items():
        target = root / path
        if target.is_file() and _normalized(target.read_text(encoding="utf-8")) == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
        changed.append(path)
    if write_pyproject(root, platform, release):
        changed.append("pyproject.toml")
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
