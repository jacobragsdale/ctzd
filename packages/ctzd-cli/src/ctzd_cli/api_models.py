"""Request and response bodies of the platform API, shared by the CLI and the API server."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ctzd_cli.appconfig import Ui


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


BuildState = Literal["queued", "running", "succeeded", "failed", "not_found"]


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
    has_screenshot: bool = False


class AppStatus(_Model):
    sbu: str
    name: str
    url: str
    release: int | None
    paused: bool
    dags: dict[str, str]
    last_build: Build | None


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
