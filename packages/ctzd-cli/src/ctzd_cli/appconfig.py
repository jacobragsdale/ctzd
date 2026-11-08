"""`app.toml`: the app's settings, sources, and jobs."""

import re
import tomllib
import zoneinfo
from pathlib import Path
from typing import Literal

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from tomlkit.items import AoT, Table

from ctzd_cli.errors import CliError

APP_FILE = "app.toml"
NAME = re.compile(r"[a-z][a-z0-9-]{1,28}[a-z0-9]")
JOB = re.compile(r"[a-z][a-z0-9_]{0,40}")
SOURCE = re.compile(r"[a-z][a-z0-9_]{0,62}")
_CRON_FIELD = re.compile(r"[\d*/,\-A-Za-z]+")
_CRON_FIELDS = 5
Size = Literal["s", "m", "l"]
Ui = Literal["react", "streamlit", "dash", "none"]

_CONFIG = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class AppSection(BaseModel):
    model_config = _CONFIG
    name: str
    sbu: str
    ui: Ui
    size: Size = "s"
    owners: tuple[str, ...] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if NAME.fullmatch(value) is None:
            msg = "app name must be 3-30 lowercase letters, digits or hyphens, starting with a letter"
            raise ValueError(msg)
        return value


class SourcesSection(BaseModel):
    model_config = _CONFIG
    use: tuple[str, ...] = ()

    @field_validator("use")
    @classmethod
    def _names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            if SOURCE.fullmatch(name) is None:
                msg = f"invalid source name {name!r}"
                raise ValueError(msg)
        return value


class Job(BaseModel):
    model_config = _CONFIG
    name: str
    schedule: str
    size: Size = "m"
    retries: int = Field(default=1, ge=0, le=5)
    timeout_minutes: int = Field(default=60, ge=1, le=24 * 60)
    # The IANA time zone the schedule runs in, such as Europe/London; Airflow follows its clock changes.
    timezone: str = "UTC"

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if JOB.fullmatch(value) is None:
            msg = "job name must be lowercase letters, digits and underscores, starting with a letter"
            raise ValueError(msg)
        return value

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, value: str) -> str:
        check_cron(value)
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        try:
            zoneinfo.ZoneInfo(value)
        except zoneinfo.ZoneInfoNotFoundError, ValueError:
            msg = f"timezone {value!r} is not an IANA time zone such as 'Europe/London' or 'America/New_York'"
            raise ValueError(msg) from None
        return value


class AppConfig(BaseModel):
    model_config = _CONFIG
    app: AppSection
    sources: SourcesSection = SourcesSection()
    jobs: tuple[Job, ...] = ()

    @field_validator("jobs")
    @classmethod
    def _unique_jobs(cls, value: tuple[Job, ...]) -> tuple[Job, ...]:
        names = [job.name for job in value]
        if len(names) != len(set(names)):
            msg = "job names must be unique"
            raise ValueError(msg)
        return value

    def job(self, name: str) -> Job:
        for job in self.jobs:
            if job.name == name:
                return job
        msg = f"app.toml has no job named {name!r}."
        raise CliError(msg, hint=f"Jobs: {', '.join(job.name for job in self.jobs) or 'none'}.")


def check_cron(value: str) -> None:
    fields = value.split()
    if len(fields) != _CRON_FIELDS or not all(_CRON_FIELD.fullmatch(field) for field in fields):
        msg = f"schedule {value!r} must be a five-field cron expression such as '0 7 * * 1-5'"
        raise ValueError(msg)


def find_root(start: Path) -> Path:
    """The nearest folder at or above `start` that holds an app.toml."""
    for folder in (start, *start.parents):
        if (folder / APP_FILE).is_file():
            return folder
    msg = "This folder is not inside a ctzd app."
    raise CliError(msg, hint="Run ctzd from the app's folder, or create one with `ctzd new`.")


def parse(text: str) -> AppConfig:
    try:
        return AppConfig.model_validate(tomllib.loads(text))
    except tomllib.TOMLDecodeError as err:
        msg = f"app.toml is not valid TOML: {err}"
        raise CliError(msg) from None
    except ValidationError as err:
        problems = "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in err.errors(include_url=False))
        msg = f"app.toml is invalid: {problems}"
        raise CliError(msg) from None


def load(root: Path) -> AppConfig:
    return parse((root / APP_FILE).read_text(encoding="utf-8"))


def render(config: AppConfig) -> str:
    document = tomlkit.document()
    app = tomlkit.table()
    app.add("name", config.app.name)
    app.add("sbu", config.app.sbu)
    app.add("ui", config.app.ui)
    app["ui"].comment("react | none")
    app.add("size", config.app.size)
    app["size"].comment("s | m | l")
    app.add("owners", list(config.app.owners))
    document.add("app", app)
    sources = tomlkit.table()
    sources.add("use", list(config.sources.use))
    sources["use"].comment("sources `ctzd env` fetches credentials for")
    document.add("sources", sources)
    if config.jobs:
        jobs = tomlkit.aot()
        for job in config.jobs:
            jobs.append(_job_table(job))
        document.add("jobs", jobs)
    return tomlkit.dumps(document)


def _job_table(job: Job) -> Table:
    table = tomlkit.table()
    table.add("name", job.name)
    table.add("schedule", job.schedule)
    table["schedule"].comment("cron, in the time zone below")
    table.add("timezone", job.timezone)
    table.add("size", job.size)
    table.add("retries", job.retries)
    table.add("timeout_minutes", job.timeout_minutes)
    return table


def add_job(root: Path, job: Job) -> AppConfig:
    """Append `job` to app.toml, keeping the file's comments and layout."""
    path = root / APP_FILE
    text = path.read_text(encoding="utf-8")
    current = parse(text)
    if any(existing.name == job.name for existing in current.jobs):
        msg = f"app.toml already has a job named {job.name!r}."
        raise CliError(msg)
    document = tomlkit.parse(text)
    jobs = document.get("jobs")
    if jobs is None:
        jobs = tomlkit.aot()
        document.add("jobs", jobs)
    if not isinstance(jobs, AoT):
        msg = "app.toml's jobs must be a list of [[jobs]] tables."
        raise CliError(msg)
    jobs.append(_job_table(job))
    updated = tomlkit.dumps(document)
    config = parse(updated)
    path.write_text(updated, encoding="utf-8")
    return config
