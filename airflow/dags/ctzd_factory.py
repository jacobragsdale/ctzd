"""One Airflow DAG per ctzd job, generated from the specs Jenkins commits next to this file.

The ctzd-dags repository holds this file and `specs/<sbu>/<app>.json`, written by
`ctzd ci render`. Both live in one git repo, so a GitDagBundle version pins the
specs together with this code. Each DAG runs its job's module in the app's own
image with KubernetesPodOperator; citizen developers never write DAG code.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.providers.smtp.notifications.smtp import SmtpNotifier
from airflow.sdk import DAG
from kubernetes.client import models as k8s
from pydantic import BaseModel, ConfigDict, ValidationError

SPECS = Path(__file__).parent / "specs"
NAMESPACE = "ctzd-apps"
SOURCES_ENV_FROM = "ctzd-sources"
SMTP_CONFIG_MAP = "ctzd-smtp"
SERVICE_ACCOUNT = "ctzd-app"
START = datetime(2026, 1, 1, tzinfo=UTC)
_LOG = logging.getLogger(__name__)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Quantities(_Spec):
    cpu: str
    memory: str


class Resources(_Spec):
    requests: Quantities
    limits: Quantities


class JobSpec(_Spec):
    dag_id: str
    name: str
    schedule: str
    retries: int
    timeout_minutes: int
    command: tuple[str, ...]
    resources: Resources


class AppSpec(_Spec):
    """The shape `ctzd ci render` writes; see packages/ctzd-cli/src/ctzd_cli/render.py."""

    version: int
    sbu: str
    app: str
    image: str
    release: int | None
    owners: tuple[str, ...]
    env: dict[str, str]
    jobs: tuple[JobSpec, ...]


def _resources(resources: Resources) -> k8s.V1ResourceRequirements:
    return k8s.V1ResourceRequirements(requests=resources.requests.model_dump(), limits=resources.limits.model_dump())


def build_dags(spec: AppSpec) -> list[DAG]:
    """The DAGs for one app's spec."""
    dags: list[DAG] = []
    for job in spec.jobs:
        notify = SmtpNotifier(to=list(spec.owners), subject=f"ctzd job failed: {job.dag_id}", html_content="Job {{ dag.dag_id }} failed in run {{ run_id }}. Logs: {{ ti.log_url }}")
        with DAG(
            dag_id=job.dag_id,
            schedule=job.schedule,
            start_date=START,
            catchup=False,
            max_active_runs=1,
            tags=["ctzd", spec.sbu, spec.app],
            default_args={"owner": ", ".join(spec.owners), "retries": job.retries, "retry_delay": timedelta(minutes=5)},
            on_failure_callback=notify,
            doc_md=f"ctzd job `{job.name}` of app `{spec.sbu}/{spec.app}`, release v{spec.release}.",
        ) as dag:
            KubernetesPodOperator(
                task_id="run",
                name=job.dag_id.replace("_", "-"),
                namespace=NAMESPACE,
                image=spec.image,
                cmds=list(job.command),
                env_vars=dict(spec.env),
                env_from=[
                    k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name=SOURCES_ENV_FROM)),
                    k8s.V1EnvFromSource(config_map_ref=k8s.V1ConfigMapEnvSource(name=SMTP_CONFIG_MAP, optional=True)),
                ],
                container_resources=_resources(job.resources),
                service_account_name=SERVICE_ACCOUNT,
                labels={"app.kubernetes.io/part-of": "ctzd", "ctzd.contoso.com/sbu": spec.sbu, "ctzd.contoso.com/app": spec.app},
                get_logs=True,
                on_finish_action="delete_succeeded_pod",
                execution_timeout=timedelta(minutes=job.timeout_minutes),
            )
        dags.append(dag)
    return dags


for _path in sorted(SPECS.glob("*/*.json")):
    try:
        _spec = AppSpec.model_validate_json(_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        # One broken spec must not take every citizen job offline: log it and build the others.
        _LOG.exception("Skipping ctzd spec %s", _path)
        continue
    for _dag in build_dags(_spec):
        globals()[_dag.dag_id] = _dag
