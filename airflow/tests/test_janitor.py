import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airflow.dag_processing.dagbag import DagBag

import ctzd_janitor

JANITOR = Path(__file__).parents[1] / "dags" / "ctzd_janitor.py"
NOW = datetime(2026, 12, 15, 3, tzinfo=UTC)


def record(folder: Path, sbu: str, app: str, deleted_at: datetime | str) -> None:
    path = folder / sbu / f"{app}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sbu": sbu, "app": app, "deleted_at": deleted_at if isinstance(deleted_at, str) else deleted_at.isoformat()}))


def test_only_apps_deleted_more_than_thirty_days_ago_lose_their_tables(tmp_path: Path) -> None:
    record(tmp_path, "fi", "old-app", NOW - timedelta(days=31))
    record(tmp_path, "fi", "edge-app", NOW - timedelta(days=30))
    record(tmp_path, "fi", "new-app", NOW - timedelta(days=29))
    removed: list[str] = []
    result = ctzd_janitor.remove_expired(tmp_path, NOW, removed.append)
    assert result == removed == ["fi/edge-app", "fi/old-app"]


def test_records_that_could_name_a_wider_folder_are_skipped(tmp_path: Path) -> None:
    long_ago = NOW - timedelta(days=90)
    record(tmp_path, "fi", "", long_ago)
    record(tmp_path, "fi", "../hr", long_ago)
    record(tmp_path, "fi", "no-timezone", "2026-01-01T00:00:00")
    (tmp_path / "fi" / "broken.json").write_text("{")
    removed: list[str] = []
    ctzd_janitor.remove_expired(tmp_path, NOW, removed.append)
    assert removed == []


def test_the_janitor_dag_loads(tmp_path: Path) -> None:
    shutil.copy(JANITOR, tmp_path / JANITOR.name)
    bag = DagBag(dag_folder=tmp_path, safe_mode=False)
    assert bag.import_errors == {}
    assert bag.dag_ids == ["ctzd_table_janitor"]
