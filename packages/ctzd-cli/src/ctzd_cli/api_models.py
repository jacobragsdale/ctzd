"""Request and response bodies of the platform API, shared by the CLI and the API server."""

import datetime as dt
from pathlib import Path, PurePosixPath
from typing import Literal

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ctzd_cli.appconfig import Ui


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


BuildState = Literal["queued", "running", "succeeded", "failed", "not_found"]
Channel = Literal["pilot", "general"]
# The folders of a platform release; see ctzd_cli/assets.
RELEASE_FOLDERS = ("locked/", "locked-react/", "guidance/", "skills/", "starter/", "start/")


def _version(value: str) -> str:
    try:
        Version(value)
    except InvalidVersion:
        msg = f"{value!r} is not a version"
        raise ValueError(msg) from None
    return value


class PackageIndex(_Model):
    name: str
    url: str
    default: bool = False
    format: str | None = None


class ReleasePython(_Model):
    """What a release writes into every app's pyproject.toml: the Python range, the build backend, the dev tool pins,
    and `[tool.uv]`'s package index and sources."""

    requires: str
    build_backend: str
    dev: tuple[str, ...]
    index: tuple[PackageIndex, ...] = Field(min_length=1)
    sources: dict[str, dict[str, str]] = Field(default_factory=dict)


class ReleaseNpm(_Model):
    """The firm's npm packages a React app's pages pin, by name: a version, or a `file:` path that may start with `~`.
    A release writes them into `web/package.json`'s dependencies and pnpm's overrides. SHIM (MVP): basis-ui's packages
    are tarballs it packs from its main branch, until the internal registry serves them."""

    packages: dict[str, str] = Field(default_factory=dict)


def expand_spec(spec: str) -> str:
    """An npm spec as pnpm reads it: `file:~/x` becomes the full path under the home folder."""
    if spec.startswith("file:~"):
        return "file:" + str(Path(spec.removeprefix("file:")).expanduser())
    return spec


class ReleaseUi(_Model):
    dependencies: tuple[str, ...]


class PlatformRelease(_Model):
    """A platform release: the build files, agent guidance, and starter code `ctzd` writes into apps."""

    release: int = Field(ge=1)
    format: int = Field(ge=1)
    requires_cli: str
    min_ctzd_core: str
    python: ReleasePython
    npm: ReleaseNpm = Field(default_factory=ReleaseNpm)
    ui: dict[Ui, ReleaseUi]
    # A breaking release changes what the checks refuse (the checkers' pins or rules), so an app that passes today may
    # fail on it. It reaches an app only once the app passes on it, or from `deadline` on; see `ctzd_cli.checks`.
    breaking: bool = False
    deadline: dt.date | None = None
    files: dict[str, str]

    @field_validator("requires_cli", "min_ctzd_core")
    @classmethod
    def _versions(cls, value: str) -> str:
        return _version(value)

    @field_validator("files")
    @classmethod
    def _paths(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value:
            parts = PurePosixPath(path).parts
            if not path.startswith(RELEASE_FOLDERS) or ".." in parts or path.startswith("/") or "\\" in path:
                msg = f"release file {path!r} is outside {', '.join(RELEASE_FOLDERS)}"
                raise ValueError(msg)
        return value


class CurrentRelease(_Model):
    """The platform release an app's channel runs, and the newest published ctzd."""

    release: int = Field(ge=1)
    channel: Channel
    cli: str | None = None

    @field_validator("cli")
    @classmethod
    def _cli(cls, value: str | None) -> str | None:
        return None if value is None else _version(value)


class Me(_Model):
    login: str


class CreateAppRequest(_Model):
    sbu: str
    name: str
    ui: Ui
    owners: tuple[str, ...] = Field(min_length=1)


class CreatedApp(_Model):
    repo: str
    clone_url: str
    url: str


class Build(_Model):
    number: int | None
    state: BuildState
    stage: str | None = None
    url: str | None = None
    log_tail: str | None = None
    release: int | None = None


class AppStatus(_Model):
    sbu: str
    name: str
    url: str
    release: int | None
    paused: bool
    dags: dict[str, str]
    last_build: Build | None
    # Jobs whose DAG Airflow has not loaded: new for a few minutes after a deploy, or refused by Airflow.
    missing_dags: tuple[str, ...] = ()


class RollbackRequest(_Model):
    release: int | None = None


class RollbackAccepted(_Model):
    release: int
    build: Build


class JobRunCreated(_Model):
    dag_id: str
    run_id: str
    url: str


JobState = Literal["queued", "running", "success", "failed"]


class JobRunState(_Model):
    dag_id: str
    run_id: str
    state: JobState
    url: str


class SourceList(_Model):
    sources: tuple[str, ...]


class LogText(_Model):
    text: str


class EnvRequest(_Model):
    sources: tuple[str, ...]


class EnvVars(_Model):
    variables: dict[str, str]
