"""Platform-owned files in an app: write them and find changes to them.

Build files (the locked files and the managed tables of pyproject.toml) are committed, and are exactly what the CLI
version in `.ctzd/version` writes: CI checks an app with that version. Guidance (`AGENTS.md` and the skills) is
gitignored, and every command rewrites it from the running CLI and the app's kit.
"""

import shutil
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tomlkit
from packaging.version import InvalidVersion, Version
from pydantic import TypeAdapter, ValidationError
from tomlkit.items import Table

from ctzd_cli import assets
from ctzd_cli.appconfig import AppConfig
from ctzd_cli.config import PackageIndex, PlatformConfig

VERSION_FILE = ".ctzd/version"
ENV_EXAMPLE = ".env.example"
GUIDANCE_DIRS = (assets.SKILLS_DIR, assets.CLAUDE_SKILLS_DIR)
# The tables of pyproject.toml the platform owns, as dotted keys.
MANAGED_PYPROJECT_KEYS = ("project.requires-python", "build-system", "dependency-groups.dev", "tool.uv")


@dataclass(frozen=True)
class Drift:
    path: str
    problem: Literal["changed", "missing"]


def tokens(platform: PlatformConfig, app: AppConfig) -> dict[str, str]:
    """What `{{token}}`s in the templates and locked files stand for, for this app."""
    sbu = platform.sbu(app.app.sbu)
    return {
        "name": platform.repo_name(app.app.sbu, app.app.name),
        "app": app.app.name,
        "title": title_for(app.app.name),
        "sbu": app.app.sbu,
        "sbu_name": sbu.name,
        "kit": sbu.kit_module,
        "kit_package": sbu.kit_package,
        "owners": ", ".join(app.app.owners),
        "owner": app.app.owners[0],
        "dev_url": platform.app_url(app.app.sbu, app.app.name, "dev"),
        "prod_url": platform.app_url(app.app.sbu, app.app.name, "prod"),
    }


def title_for(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("-"))


def recorded_version(root: Path) -> Version | None:
    """The CLI version that last wrote the app's build files; None when nothing usable is recorded."""
    path = root / VERSION_FILE
    if not path.is_file():
        return None
    try:
        return Version(path.read_text(encoding="utf-8").strip())
    except InvalidVersion:
        return None


def expected_files(platform: PlatformConfig, app: AppConfig, kit: Mapping[str, str] | None = None) -> dict[str, str]:
    """Every build file of the app as this CLI writes it, `.ctzd/version` among them. `kit`, the token values that come
    from the app's installed kit, adds the files that need them."""
    filled = tokens(platform, app) | dict(kit or {})
    return assets.locked_files(app.app.kind, filled, kit=kit is not None) | {VERSION_FILE: f"{assets.version()}\n"}


def python_index(platform: PlatformConfig) -> tuple[PackageIndex, ...]:
    overrides = platform.build_overrides
    return assets.build().python.index if overrides is None or overrides.python_index is None else overrides.python_index


def managed_pyproject(platform: PlatformConfig) -> dict[str, object]:
    """The parts of pyproject.toml the platform owns, as dotted keys and their values."""
    python = assets.build().python
    uv: dict[str, object] = {
        "build-backend": {"module-name": "app"},
        "index": [index.model_dump(exclude_none=True, exclude_defaults=True) | {"name": index.name, "url": index.url} for index in python_index(platform)],
    }
    values: dict[str, object] = {
        "project.requires-python": python.requires,
        "build-system": {"requires": [python.build_backend], "build-backend": "uv_build"},
        "dependency-groups.dev": list(python.dev),
        "tool.uv": uv,
    }
    return {key: values[key] for key in MANAGED_PYPROJECT_KEYS}


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n")


_TABLE: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def _lookup(document: Mapping[str, object], dotted: str) -> object:
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


def drift(root: Path, platform: PlatformConfig, app: AppConfig, kit: Mapping[str, str] | None = None) -> list[Drift]:
    problems: list[Drift] = []
    for path, text in sorted(expected_files(platform, app, kit).items()):
        target = root / path
        if not target.is_file():
            problems.append(Drift(path, "missing"))
        elif _normalized(target.read_text(encoding="utf-8")) != text:
            problems.append(Drift(path, "changed"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")) if (root / "pyproject.toml").is_file() else {}
    for key, value in managed_pyproject(platform).items():
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


def write_pyproject(root: Path, platform: PlatformConfig) -> bool:
    path = root / "pyproject.toml"
    before = path.read_text(encoding="utf-8")
    document = tomlkit.parse(before)
    for key, value in managed_pyproject(platform).items():
        _set_dotted(document, key, value)
    after = tomlkit.dumps(document)
    if after != before:
        path.write_text(after, encoding="utf-8")
    return after != before


def write_build(root: Path, platform: PlatformConfig, app: AppConfig, kit: Mapping[str, str] | None = None) -> list[str]:
    """Bring every build file to this CLI's and record its version; return the paths that changed."""
    changed: list[str] = []
    for path, text in expected_files(platform, app, kit).items():
        target = root / path
        if target.is_file() and _normalized(target.read_text(encoding="utf-8")) == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
        changed.append(path)
    if write_pyproject(root, platform):
        changed.append("pyproject.toml")
    return sorted(changed)


def env_example(app: AppConfig) -> str:
    lines = ["# Written by ctzd sync. `ctzd env` writes the real .env with these sources' dev credentials; never open or commit .env.", *(f"# {name}" for name in app.sources.use), ""]
    return "\n".join(lines)


def write_env_example(root: Path, app: AppConfig) -> bool:
    example = root / ENV_EXAMPLE
    if example.is_file() and example.read_text(encoding="utf-8") == env_example(app):
        return False
    example.write_text(env_example(app), encoding="utf-8")
    return True


def write_guidance(root: Path, extra: Mapping[str, str] | None = None) -> bool:
    """Make `AGENTS.md` and both skill folders exactly this CLI's, with `extra` (the kit's department skill) added;
    return whether anything changed."""
    expected = assets.guidance_files() | dict(extra or {})
    present = {file.relative_to(root).as_posix() for folder in GUIDANCE_DIRS if (root / folder).is_dir() for file in (root / folder).rglob("*") if file.is_file()}
    stale = [path for path, text in expected.items() if not (root / path).is_file() or (root / path).read_text(encoding="utf-8") != text]
    if not (present - expected.keys()) and not stale:
        return False
    for folder in GUIDANCE_DIRS:
        if (root / folder).exists():
            shutil.rmtree(root / folder)
    for path, text in expected.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
    return True
