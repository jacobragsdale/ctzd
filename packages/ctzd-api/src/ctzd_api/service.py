"""What each API call does, written against the gateway protocols so it can run on fakes."""

import re
from dataclasses import dataclass
from typing import Literal

from ctzd_api.gateways import Airflow, GitHub, Jenkins, JenkinsBuild, Kube, Permission, Secrets
from ctzd_api.releases import BrokenReleaseError, ReleaseStore
from ctzd_cli import api_models as m
from ctzd_cli import appconfig, release
from ctzd_cli.appconfig import AppSection
from ctzd_cli.config import PlatformConfig, dag_id, source_secret
from ctzd_cli.errors import CliError

ROLLBACK_JOB = "ctzd-rollback"
DELETE_JOB = "ctzd-delete"
SHARED_STORE_SAS_ENTRY = "ctzd-shared-store-sas"
_RELEASE = re.compile(r"release v(\d+)")
_LEVELS: dict[Permission, int] = {"none": 0, "read": 1, "triage": 1, "write": 2, "maintain": 2, "admin": 3}
Needed = Literal["read", "write", "admin"]
_NEEDED: dict[Needed, int] = {"read": 1, "write": 2, "admin": 3}


class ApiError(Exception):
    status = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ForbiddenError(ApiError):
    status = 403


class NotFoundError(ApiError):
    status = 404


class ConflictError(ApiError):
    status = 409


class UpstreamError(ApiError):
    status = 502


@dataclass(frozen=True)
class AppRef:
    sbu: str
    name: str


@dataclass(frozen=True)
class Services:
    github: GitHub
    jenkins: Jenkins
    airflow: Airflow
    kube: Kube
    secrets: Secrets
    releases: ReleaseStore
    platform: PlatformConfig


def _repo(services: Services, app: AppRef) -> str:
    if app.sbu not in services.platform.sbus:
        msg = f"Unknown business unit {app.sbu!r}."
        raise NotFoundError(msg)
    return services.platform.repo_name(app.sbu, app.name)


def require(services: Services, login: str, repo: str, needed: Needed) -> None:
    if not services.github.repo_exists(repo):
        msg = f"There is no app {repo}."
        raise NotFoundError(msg)
    if _LEVELS[services.github.permission(repo, login)] < _NEEDED[needed]:
        msg = f"{login} needs {needed} access to {repo}."
        raise ForbiddenError(msg)


def create_app(services: Services, login: str, request: m.CreateAppRequest) -> m.CreatedApp:
    try:
        AppSection(name=request.name, sbu=request.sbu, ui=request.ui, owners=request.owners)
    except ValueError as err:
        raise ApiError(str(err)) from None
    sbu = services.platform.sbus.get(request.sbu)
    if sbu is None:
        msg = f"Unknown business unit {request.sbu!r}."
        raise ApiError(msg)
    if not services.github.is_org_member(login):
        msg = f"{login} is not a member of {services.platform.github.org}."
        raise ForbiddenError(msg)
    repo = services.platform.repo_name(request.sbu, request.name)
    if services.github.repo_exists(repo):
        msg = f"An app named {repo} already exists."
        raise ConflictError(msg)
    services.github.create_repo(repo, admin=login, team=sbu.github_team)
    github = services.platform.github
    return m.CreatedApp(repo=f"{github.org}/{repo}", clone_url=f"https://{github.host}/{github.org}/{repo}.git", url=services.platform.app_url(request.sbu, request.name))


def _app_config(services: Services, repo: str) -> appconfig.AppConfig | None:
    text = services.github.file(repo, appconfig.APP_FILE)
    if text is None:
        return None
    try:
        return appconfig.parse(text)
    except CliError:
        return None


def to_build(build: JenkinsBuild | None, *, log_tail: str | None) -> m.Build:
    if build is None:
        return m.Build(number=None, state="not_found")
    if build.building:
        state: m.BuildState = "running"
    elif build.result is None:
        state = "queued"
    elif build.result == "SUCCESS":
        state = "succeeded"
    else:
        state = "failed"
    found = _RELEASE.search(build.description or "")
    return m.Build(number=build.number, state=state, stage=build.failed_stage, url=build.url, log_tail=log_tail if state == "failed" else None, release=None if found is None else int(found.group(1)))


def status(services: Services, login: str, app: AppRef) -> m.AppStatus:
    repo = _repo(services, app)
    require(services, login, repo, "read")
    state = services.kube.state(repo)
    config = _app_config(services, repo)
    jobs = () if config is None else config.jobs
    builds = services.jenkins.builds(repo)
    last = max(builds, key=lambda build: build.number, default=None)
    return m.AppStatus(
        sbu=app.sbu,
        name=app.name,
        url=services.platform.app_url(app.sbu, app.name),
        release=state.release,
        paused=state.paused,
        dags={job.name: services.platform.dag_url(app.sbu, app.name, job.name) for job in jobs},
        last_build=to_build(last, log_tail=None),
    )


def resume(services: Services, login: str, app: AppRef) -> None:
    repo = _repo(services, app)
    require(services, login, repo, "write")
    services.kube.set_paused(repo, paused=False)


def build_for_commit(services: Services, login: str, app: AppRef, commit: str) -> m.Build:
    repo = _repo(services, app)
    require(services, login, repo, "read")
    matching = [build for build in services.jenkins.builds(repo) if build.commit == commit]
    build = max(matching, key=lambda item: item.number, default=None)
    tail = services.jenkins.console_tail(build, 60) if build is not None and not build.building and build.result not in {None, "SUCCESS"} else None
    return to_build(build, log_tail=tail)


def rollback(services: Services, login: str, app: AppRef, target: int | None) -> m.RollbackAccepted:
    repo = _repo(services, app)
    require(services, login, repo, "write")
    known = sorted(item.release for item in release.parse_tags(services.github.release_tags(repo)))
    current = services.kube.state(repo).release
    if target is None:
        earlier = [number for number in known if current is None or number < current]
        if not earlier:
            msg = f"{repo} has no release before v{current} to roll back to."
            raise ConflictError(msg)
        target = earlier[-1]
    if target not in known:
        msg = f"{repo} has no release v{target}."
        raise NotFoundError(msg)
    services.kube.set_paused(repo, paused=True)
    services.jenkins.trigger(ROLLBACK_JOB, {"REPO": repo, "RELEASE": str(target)})
    return m.RollbackAccepted(release=target, build=m.Build(number=None, state="queued"))


def run_job(services: Services, login: str, app: AppRef, job: str) -> m.JobRunCreated:
    repo = _repo(services, app)
    require(services, login, repo, "write")
    config = _app_config(services, repo)
    if config is None or job not in {item.name for item in config.jobs}:
        msg = f"{repo} has no deployed job {job!r}; deploy it first."
        raise NotFoundError(msg)
    dag = dag_id(app.sbu, app.name, job)
    run_id = services.airflow.trigger(dag)
    return m.JobRunCreated(dag_id=dag, run_id=run_id, url=f"{services.platform.dag_url(app.sbu, app.name, job)}/runs/{run_id}")


_RUN_STATES: dict[str, m.JobState] = {"queued": "queued", "running": "running", "success": "success", "failed": "failed", "upstream_failed": "failed"}


def job_run(services: Services, login: str, app: AppRef, job: str, run_id: str) -> m.JobRunState:
    repo = _repo(services, app)
    require(services, login, repo, "read")
    dag = dag_id(app.sbu, app.name, job)
    state = _RUN_STATES.get(services.airflow.run_state(dag, run_id), "running")
    return m.JobRunState(dag_id=dag, run_id=run_id, state=state, url=f"{services.platform.dag_url(app.sbu, app.name, job)}/runs/{run_id}")


SOURCE_PREFIX = source_secret("")


def sources(services: Services) -> m.SourceList:
    names = sorted(name.removeprefix(SOURCE_PREFIX).replace("-", "_") for name in services.secrets.names(SOURCE_PREFIX))
    return m.SourceList(sources=tuple(names))


def logs(services: Services, login: str, app: AppRef, *, job: str | None, tail: int) -> m.LogText:
    repo = _repo(services, app)
    require(services, login, repo, "read")
    text = services.kube.dashboard_log(repo, tail) if job is None else services.airflow.latest_task_log(dag_id(app.sbu, app.name, job), tail)
    return m.LogText(text=text)


def env(services: Services, login: str, app: AppRef, sources: tuple[str, ...]) -> m.EnvVars:
    repo = _repo(services, app)
    require(services, login, repo, "write")
    variables: dict[str, str] = {}
    for source in sources:
        if appconfig.SOURCE.fullmatch(source) is None:
            msg = f"Invalid source name {source!r}."
            raise ApiError(msg)
        value = services.secrets.get(source_secret(source))
        if value is None:
            msg = f"Source {source!r} does not exist. Ask the platform team to add it."
            raise NotFoundError(msg)
        variables[f"CTZD_SOURCE_{source.upper()}"] = value
    sas = services.secrets.get(SHARED_STORE_SAS_ENTRY)
    if sas is not None:
        variables["CTZD_SHARED_STORE_SAS"] = sas
    return m.EnvVars(variables=variables)


def delete(services: Services, login: str, app: AppRef) -> None:
    repo = _repo(services, app)
    require(services, login, repo, "admin")
    services.jenkins.trigger(DELETE_JOB, {"REPO": repo})
    services.github.archive_repo(repo)


def current_release(services: Services, app: AppRef | None) -> m.CurrentRelease:
    """Any logged-in person may ask; `ctzd new` asks before the app's repo exists, `ctzd setup` without an app."""
    repo = None if app is None else _repo(services, app)
    try:
        return services.releases.channels().current(repo)
    except BrokenReleaseError as err:
        raise UpstreamError(str(err)) from None


def platform_release(services: Services, number: int) -> m.PlatformRelease:
    try:
        found = services.releases.release(number)
    except BrokenReleaseError as err:
        raise UpstreamError(str(err)) from None
    if found is None:
        msg = f"There is no platform release {number}."
        raise NotFoundError(msg)
    return found
