import json
import shutil
from pathlib import Path

import pytest
from airflow.dag_processing.dagbag import DagBag
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

FACTORY = Path(__file__).parents[1] / "dags" / "ctzd_factory.py"
RESOURCES = {"requests": {"cpu": "100m", "memory": "1Gi"}, "limits": {"cpu": "1", "memory": "4Gi"}}


def spec(app: str, *jobs: str) -> dict[str, object]:
    return {
        "version": 1,
        "sbu": "fi",
        "app": app,
        "image": f"contosoctzd.azurecr.io/apps/fi-{app}:abc123",
        "release": 4,
        "owners": ["ann.lee@contoso.com"],
        "env": {"CTZD_APP": app, "CTZD_SBU": "fi", "CTZD_ENV": "cluster", "CTZD_STORE_URL": "abfs://ctzd@x.dfs.core.windows.net"},
        "jobs": [
            {
                "dag_id": f"fi__{app.replace('-', '_')}__{job}",
                "name": job,
                "schedule": "0 6 * * 1-5",
                "retries": 2,
                "timeout_minutes": 30,
                "command": ["python", "-m", f"app.jobs.{job}"],
                "resources": RESOURCES,
            }
            for job in jobs
        ],
    }


def load(tmp_path: Path, specs: dict[str, object]) -> DagBag:
    shutil.copy(FACTORY, tmp_path / FACTORY.name)
    for name, body in specs.items():
        path = tmp_path / "specs" / "fi" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body if isinstance(body, str) else json.dumps(body))
    return DagBag(dag_folder=tmp_path, safe_mode=False)


def test_each_job_becomes_a_dag_running_its_module(tmp_path: Path) -> None:
    bag = load(tmp_path, {"bond-prices": spec("bond-prices", "refresh", "summary")})
    assert bag.import_errors == {}
    assert sorted(bag.dag_ids) == ["fi__bond_prices__refresh", "fi__bond_prices__summary"]
    dag = bag.dags["fi__bond_prices__refresh"]  # get_dag() reads the metadata database; the parsed DAGs are enough here
    (task,) = dag.tasks
    assert isinstance(task, KubernetesPodOperator)
    assert task.image == "contosoctzd.azurecr.io/apps/fi-bond-prices:abc123"
    assert task.cmds == ["python", "-m", "app.jobs.refresh"]
    assert task.retries == 2
    assert dag.max_active_runs == 1
    assert not dag.catchup


def test_a_broken_spec_leaves_the_other_apps_jobs_running(tmp_path: Path) -> None:
    bag = load(tmp_path, {"bond-prices": spec("bond-prices", "refresh"), "broken": "{not json", "missing-field": {"version": 1}})
    assert bag.import_errors == {}
    assert bag.dag_ids == ["fi__bond_prices__refresh"]


@pytest.mark.parametrize("jobs", [(), ("refresh",)])
def test_dashboard_only_apps_add_no_dags(tmp_path: Path, jobs: tuple[str, ...]) -> None:
    bag = load(tmp_path, {"bond-prices": spec("bond-prices", *jobs)})
    assert len(bag.dag_ids) == len(jobs)
