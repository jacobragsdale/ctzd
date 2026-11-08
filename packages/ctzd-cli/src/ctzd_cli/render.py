"""`ctzd ci render`: turn app.toml into Helm values for the dashboard and an Airflow spec for the jobs."""

from pydantic import BaseModel, ConfigDict, Field

from ctzd_cli.appconfig import AppConfig
from ctzd_cli.config import PlatformConfig, dag_id, source_secret

PORT = 8080
_STREAMLIT = (
    "streamlit",
    "run",
    "src/app/ui/main.py",
    f"--server.port={PORT}",
    "--server.address=0.0.0.0",
    "--server.headless=true",
    "--browser.gatherUsageStats=false",
    "--client.toolbarMode=viewer",
)
_DASH = ("gunicorn", "app.ui.main:server", f"--bind=0.0.0.0:{PORT}", "--workers=2", "--threads=4", "--timeout=120")
# One process serves the datasets under /api and the built pages at /.
_REACT = ("uvicorn", "--factory", "ctzd.api:asgi", "--host=0.0.0.0", f"--port={PORT}", "--proxy-headers")
_COMMANDS = {"react": _REACT, "streamlit": _STREAMLIT, "dash": _DASH}
_READINESS = {"react": "/api/health/ready", "streamlit": "/_stcore/health"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False, populate_by_name=True)


class Quantities(_Model):
    cpu: str
    memory: str


class Resources(_Model):
    requests: Quantities
    limits: Quantities


class HelmValues(_Model):
    """Values for the ctzd-app chart. `enabled` is false for apps without a dashboard."""

    enabled: bool
    app: str
    sbu: str
    fullname: str
    image: str
    release: int | None
    host: str
    port: int
    command: tuple[str, ...]
    readiness_path: str = Field(alias="readinessPath")
    resources: Resources
    env: dict[str, str]
    # One Kubernetes secret per source the app lists; the pod gets no other source's credentials.
    source_secrets: tuple[str, ...] = Field(alias="sourceSecrets")


class JobSpec(_Model):
    dag_id: str
    name: str
    schedule: str
    retries: int
    timeout_minutes: int
    command: tuple[str, ...]
    resources: Resources
    timezone: str


class AirflowSpec(_Model):
    """The file Jenkins commits to ctzd-dags as `specs/<sbu>/<app>.json`; the factory DAG reads it."""

    version: int = 2
    sbu: str
    app: str
    image: str
    release: int | None
    owners: tuple[str, ...]
    env: dict[str, str]
    source_secrets: tuple[str, ...]
    jobs: tuple[JobSpec, ...]


def runtime_env(platform: PlatformConfig, app: AppConfig) -> dict[str, str]:
    return {"CTZD_APP": app.app.name, "CTZD_SBU": app.app.sbu, "CTZD_ENV": "cluster", "CTZD_STORE_URL": platform.platform.store_url}


def job_command(name: str) -> tuple[str, ...]:
    """Run a job by calling its main(): `python -m` would do nothing for a module without the __main__ guard."""
    return ("python", "-c", f"import sys; from app.jobs.{name} import main; sys.exit(main())")


def source_secrets(app: AppConfig) -> tuple[str, ...]:
    return tuple(source_secret(source) for source in app.sources.use)


def _resources(platform: PlatformConfig, size: str) -> Resources:
    chosen = platform.sizes[size]
    return Resources(requests=Quantities(cpu=chosen.cpu_request, memory=chosen.memory_request), limits=Quantities(cpu=chosen.cpu_limit, memory=chosen.memory_limit))


def helm_values(platform: PlatformConfig, app: AppConfig, *, image: str, release: int | None) -> HelmValues:
    ui = app.app.ui
    fullname = platform.repo_name(app.app.sbu, app.app.name)
    return HelmValues(
        enabled=ui != "none",
        app=app.app.name,
        sbu=app.app.sbu,
        fullname=fullname,
        image=image,
        release=release,
        host=f"{fullname}.{platform.platform.apps_domain}",
        port=PORT,
        command=_COMMANDS.get(ui, ()),
        readiness_path=_READINESS.get(ui, "/healthz"),
        resources=_resources(platform, app.app.size),
        env=runtime_env(platform, app),
        source_secrets=source_secrets(app),
    )


def airflow_spec(platform: PlatformConfig, app: AppConfig, *, image: str, release: int | None) -> AirflowSpec:
    return AirflowSpec(
        sbu=app.app.sbu,
        app=app.app.name,
        image=image,
        release=release,
        owners=app.app.owners,
        env=runtime_env(platform, app),
        source_secrets=source_secrets(app),
        jobs=tuple(
            JobSpec(
                dag_id=dag_id(app.app.sbu, app.app.name, job.name),
                name=job.name,
                schedule=job.schedule,
                retries=job.retries,
                timeout_minutes=job.timeout_minutes,
                command=job_command(job.name),
                timezone=job.timezone,
                resources=_resources(platform, job.size),
            )
            for job in app.jobs
        ),
    )
