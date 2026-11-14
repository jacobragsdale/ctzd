"""What ships inside the CLI and goes into apps (`ctzd_cli/assets`), keyed by its path in the app.

- `locked/common/` and `locked/<kind>/`: the build files, committed in every app and drift-checked against the CLI
  version the app records in `.ctzd/version`;
- `guidance/` and `skills/`: `AGENTS.md` and the common skills, gitignored, rewritten by every command;
- `templates/<kind>/`: what `ctzd new` writes once, its `{{token}}`s filled;
- `build.toml`: the managed tables of every app's `pyproject.toml`;
- `start/GUIDE.md`: what `ctzd setup` hands the agent.
"""

import functools
import tomllib
from collections.abc import Mapping
from importlib import metadata, resources
from importlib.resources.abc import Traversable

from pydantic import BaseModel, ConfigDict, Field

from ctzd_cli.appconfig import Kind
from ctzd_cli.config import PackageIndex

SKILLS_DIR = ".agents/skills"
CLAUDE_SKILLS_DIR = ".claude/skills"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class BuildPython(_Model):
    requires: str
    build_backend: str
    dev: tuple[str, ...]
    index: tuple[PackageIndex, ...] = Field(min_length=1)


class BuildKind(_Model):
    dependencies: tuple[str, ...]


class Build(_Model):
    python: BuildPython
    kinds: dict[Kind, BuildKind]


def version() -> str:
    return metadata.version("ctzd")


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


@functools.cache
def bundle() -> dict[str, str]:
    """Every file under `ctzd_cli/assets`, by its path there."""
    return _tree(resources.files("ctzd_cli").joinpath("assets"))


@functools.cache
def build() -> Build:
    return Build.model_validate(tomllib.loads(bundle()["build.toml"]))


def _folder(prefix: str) -> dict[str, str]:
    return {path.removeprefix(prefix): text for path, text in sorted(bundle().items()) if path.startswith(prefix)}


def render(text: str, tokens: Mapping[str, str]) -> str:
    for token, value in tokens.items():
        text = text.replace("{{" + token + "}}", value)
    return text


def locked_files(kind: Kind, tokens: Mapping[str, str]) -> dict[str, str]:
    """The build files of an app of `kind`, their `{{token}}`s filled from the app and its kit."""
    return {path: render(text, tokens) for path, text in (_folder("locked/common/") | _folder(f"locked/{kind}/")).items()}


def guidance_files() -> dict[str, str]:
    """`AGENTS.md` and the common skills, under both skill folders: Claude Code does not read `.agents/skills/`."""
    files = _folder("guidance/")
    for path, text in _folder("skills/common/").items():
        files[f"{SKILLS_DIR}/{path}"] = text
        files[f"{CLAUDE_SKILLS_DIR}/{path}"] = text
    return files


def template_files(kind: Kind, tokens: Mapping[str, str]) -> dict[str, str]:
    """What `ctzd new` writes for an app of `kind`, its `{{token}}`s filled."""
    return {path: render(text, tokens) for path, text in _folder(f"templates/{kind}/").items()}


def start_guide() -> str:
    return bundle()["start/GUIDE.md"]
