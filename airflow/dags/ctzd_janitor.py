"""Remove a deleted app's tables from the store 30 days after `ctzd delete`.

The delete job records each deletion as `deleted/<sbu>/<app>.json` in the
ctzd-dags repository, next to this file. Once a day this DAG removes
`<sbu>/<app>/` from the store for every deletion older than the grace period.
Removing a folder that is already gone does nothing, so reruns are safe.
"""

import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airflow.providers.microsoft.azure.hooks.data_lake import AzureDataLakeStorageV2Hook
from airflow.sdk import DAG, task
from azure.core.exceptions import ResourceNotFoundError
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError, field_validator

DELETED = Path(__file__).parent / "deleted"
GRACE = timedelta(days=30)
# The Airflow connection to the store (an ADLS Gen2 account) and the file system that holds `<sbu>/<app>/`.
STORE_CONNECTION = "ctzd_store"
STORE_FILESYSTEM = "ctzd"
_SBU = re.compile(r"[a-z][a-z0-9]*")
_APP = re.compile(r"[a-z][a-z0-9-]{1,28}[a-z0-9]")
_LOG = logging.getLogger(__name__)


class Deletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sbu: str
    app: str
    deleted_at: AwareDatetime

    @field_validator("sbu")
    @classmethod
    def _sbu(cls, value: str) -> str:
        if _SBU.fullmatch(value) is None:
            msg = f"invalid business unit {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("app")
    @classmethod
    def _app(cls, value: str) -> str:
        # Never empty and never a path: an empty name would remove the whole business unit's tables.
        if _APP.fullmatch(value) is None:
            msg = f"invalid app name {value!r}"
            raise ValueError(msg)
        return value

    @property
    def folder(self) -> str:
        return f"{self.sbu}/{self.app}"


def read_deletions(folder: Path) -> list[Deletion]:
    """Every deletion record; an unreadable one is logged and skipped, never guessed at."""
    found: list[Deletion] = []
    for path in sorted(folder.glob("*/*.json")):
        try:
            found.append(Deletion.model_validate_json(path.read_bytes()))
        except (OSError, ValidationError):
            _LOG.exception("Skipping deletion record %s", path)
    return found


def expired(deletions: Iterable[Deletion], now: datetime, grace: timedelta = GRACE) -> list[Deletion]:
    return [deletion for deletion in deletions if deletion.deleted_at.astimezone(UTC) + grace <= now]


def remove_expired(folder: Path, now: datetime, remove: Callable[[str], None]) -> list[str]:
    """Remove the store folder of every app deleted more than GRACE ago; return the folders removed."""
    removed: list[str] = []
    for deletion in expired(read_deletions(folder), now):
        remove(deletion.folder)
        removed.append(deletion.folder)
    return removed


def _remove_from_store(folder: str) -> None:
    client = AzureDataLakeStorageV2Hook(adls_conn_id=STORE_CONNECTION).get_conn().get_file_system_client(STORE_FILESYSTEM)
    try:
        client.get_directory_client(folder).delete_directory()
    except ResourceNotFoundError:
        _LOG.info("%s is already gone", folder)


with DAG(dag_id="ctzd_table_janitor", schedule="0 3 * * *", start_date=datetime(2026, 1, 1, tzinfo=UTC), catchup=False, max_active_runs=1, tags=["ctzd", "platform"], doc_md=__doc__):

    @task
    def remove_deleted_apps_tables() -> list[str]:
        return remove_expired(DELETED, datetime.now(UTC), _remove_from_store)

    remove_deleted_apps_tables()
