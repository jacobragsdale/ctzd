"""Platform-owned files: write them (`ctzd sync`) and detect changes to them (the drift check)."""

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tomlkit
from packaging.version import Version
from pydantic import TypeAdapter, ValidationError
from tomlkit.items import Table

from ctzd_cli import assets
from ctzd_cli.appconfig import AppConfig
from ctzd_cli.config import PlatformConfig
from ctzd_cli.errors import CliError

VERSION_FILE = ".ctzd/version"
SKILLS_DIR = ".agents/skills"
CLAUDE_SKILLS_DIR = ".claude/skills"
ENV_EXAMPLE = ".env.example"


@dataclass(frozen=True)
class Drift:
    path: str
    problem: Literal["changed", "missing", "unexpected"]


def expected_files(app: AppConfig, version: str) -> dict[str, str]:
    return assets.locked_files(app.app.sbu) | {VERSION_FILE: f"{version}\n"}


def managed_pyproject(platform: PlatformConfig) -> dict[str, object]:
    """The parts of pyproject.toml the platform owns, as dotted keys and their values."""
    uv: dict[str, object] = {
        "build-backend": {"module-name": "app"},
        "index": [index.model_dump(exclude_none=True, exclude_defaults=True) | {"name": index.name, "url": index.url} for index in platform.python.index],
    }
    if platform.python.sources:
        uv["sources"] = dict(platform.python.sources)
    return {
        "project.requires-python": platform.python.requires,
        "build-system": {"requires": [platform.python.build_backend], "build-backend": "uv_build"},
        "dependency-groups.dev": list(platform.python.dev),
        "tool.uv": uv,
    }


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


def drift(root: Path, app: AppConfig, platform: PlatformConfig, version: str) -> list[Drift]:
    problems: list[Drift] = []
    expected = expected_files(app, version)
    for path, text in sorted(expected.items()):
        target = root / path
        if not target.is_file():
            problems.append(Drift(path, "missing"))
        elif _normalized(target.read_text(encoding="utf-8")) != text:
            problems.append(Drift(path, "changed"))
    skills_root = root / SKILLS_DIR
    if skills_root.is_dir():
        for file in sorted(skills_root.rglob("*")):
            relative = file.relative_to(root).as_posix()
            if file.is_file() and relative not in expected:
                problems.append(Drift(relative, "unexpected"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
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


def env_example(app: AppConfig) -> str:
    lines = [
        "# Written by ctzd sync. `ctzd env` fills a real .env with these variables; never commit .env.",
        *(f"CTZD_SOURCE_{name.upper()}=" for name in app.sources.use),
        "CTZD_SHARED_STORE_SAS=",
        "",
    ]
    return "\n".join(lines)


def mirror_skills_for_claude(root: Path) -> None:
    """Claude Code loads skills only from .claude/skills, so keep a gitignored copy there."""
    source = root / SKILLS_DIR
    target = root / CLAUDE_SKILLS_DIR
    if target.exists():
        shutil.rmtree(target)
    if source.is_dir():
        shutil.copytree(source, target)


def write(root: Path, app: AppConfig, platform: PlatformConfig, version: str) -> list[str]:
    """Bring every platform-owned file to `version`; return the paths that changed."""
    changed: list[str] = []
    expected = expected_files(app, version)
    for path, text in expected.items():
        target = root / path
        if target.is_file() and _normalized(target.read_text(encoding="utf-8")) == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
        changed.append(path)
    skills_root = root / SKILLS_DIR
    for file in sorted(skills_root.rglob("*"), reverse=True):
        relative = file.relative_to(root).as_posix()
        if file.is_file() and relative not in expected:
            file.unlink()
            changed.append(relative)
        elif file.is_dir() and not any(file.iterdir()):
            file.rmdir()
    if write_pyproject(root, platform):
        changed.append("pyproject.toml")
    example = root / ENV_EXAMPLE
    if not example.is_file() or example.read_text(encoding="utf-8") != env_example(app):
        example.write_text(env_example(app), encoding="utf-8")
        changed.append(ENV_EXAMPLE)
    mirror_skills_for_claude(root)
    return sorted(changed)


def recorded_version(root: Path) -> str | None:
    path = root / VERSION_FILE
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def compare_versions(recorded: str | None, installed: str) -> Literal["same", "behind", "ahead"]:
    """`behind`: the app's platform files are older than this CLI. `ahead`: this CLI is older than the app."""
    if recorded is None:
        return "behind"
    if Version(recorded) == Version(installed):
        return "same"
    return "behind" if Version(recorded) < Version(installed) else "ahead"


def require_not_ahead(root: Path, installed: str) -> None:
    recorded = recorded_version(root)
    if compare_versions(recorded, installed) == "ahead":
        msg = f"This app uses platform version {recorded}, newer than the installed ctzd {installed}."
        raise CliError(msg, hint="Run `uv tool upgrade ctzd`, then try again.")
