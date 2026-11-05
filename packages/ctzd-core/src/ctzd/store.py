"""The table store: parquet tables a job writes and a dashboard reads.

Layout: `<root>/<sbu>/<app>/<table>/v=<version>/data.parquet`, plus a
`_latest` file naming the current version. A write lands in a new version
folder and then replaces `_latest`, so a reader never sees a half-written
table, and the previous versions stay available.
"""

import re
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from ctzd import _overrides
from ctzd._arrow import Storage
from ctzd._errors import StoreError
from ctzd.settings import app_settings

_TABLE = re.compile(r"[a-z][a-z0-9_]{0,62}")
_LATEST = "_latest"
KEEP_VERSIONS = 10


def storage_for(url: str, sas_token: str | None) -> Storage:
    """Open `abfs://container@account.dfs.core.windows.net/prefix` or a local path."""
    parts = urlsplit(url)
    if parts.scheme == "abfs":
        if parts.username is None or parts.hostname is None:
            msg = f"Store URL {url!r} must look like abfs://container@account.dfs.core.windows.net/prefix."
            raise StoreError(msg)
        account = parts.hostname.split(".", 1)[0]
        return Storage.azure(account=account, container_path=parts.username + parts.path, sas_token=sas_token)
    if parts.scheme in {"", "file"}:
        root = Path(parts.path if parts.scheme == "file" else url).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return Storage.local(root.as_posix())
    msg = f"Unsupported store URL scheme {parts.scheme!r}; use abfs:// or a local path."
    raise StoreError(msg)


def _target() -> _overrides.StoreTarget:
    override = _overrides.current().store
    if override is not None:
        return override
    settings = app_settings()
    shared = None
    if settings.shared_store_url is not None:
        sas = None if settings.shared_store_sas is None else settings.shared_store_sas.get_secret_value()
        shared = storage_for(settings.shared_store_url, sas)
    return _overrides.StoreTarget(storage=storage_for(settings.store_url, None), sbu=settings.sbu, app=settings.app, shared=shared)


def _check_table(name: str) -> str:
    if _TABLE.fullmatch(name) is None:
        msg = f"Invalid table name {name!r}: use lowercase letters, digits and underscores, starting with a letter."
        raise StoreError(msg)
    return name


def _utc_now() -> datetime:
    return datetime.now(UTC)


def write_table(frame: pd.DataFrame, name: str, *, allow_empty: bool = False, keep: int = KEEP_VERSIONS, now: Callable[[], datetime] = _utc_now) -> str:
    """Publish `frame` as the new version of this app's table `name` and return the version id.

    An empty frame is refused unless `allow_empty=True`, because an empty
    result is far more often a broken query than a real answer.
    """
    _check_table(name)
    if frame.empty and not allow_empty:
        msg = f"Refusing to publish an empty {name!r}; pass allow_empty=True if no rows is a valid result."
        raise StoreError(msg)
    if keep < 1:
        msg = "keep must be at least 1."
        raise StoreError(msg)
    stamp = now()
    if stamp.utcoffset() is None:
        msg = "now() must return a timezone-aware datetime."
        raise StoreError(msg)
    version = stamp.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = _target()
    storage = target.storage
    table_dir = storage.path(target.sbu, target.app, name)
    version_dir = f"{table_dir}/v={version}"
    storage.make_dirs(version_dir)
    storage.write_frame(f"{version_dir}/data.parquet", frame)
    pointer_tmp = f"{table_dir}/{_LATEST}.{secrets.token_hex(4)}.tmp"
    storage.write_text(pointer_tmp, version)
    storage.move(pointer_tmp, f"{table_dir}/{_LATEST}")
    versions = [entry for entry in storage.child_dirs(table_dir) if entry.startswith("v=")]
    for old in versions[:-keep]:
        storage.delete_dir(f"{table_dir}/{old}")
    return version


def read_table(name: str, *, app: str | None = None, sbu: str | None = None, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Read the latest version of table `name`, from this app or from `app` (and `sbu`)."""
    _check_table(name)
    target = _target()
    owner_sbu = target.sbu if sbu is None else sbu
    owner_app = target.app if app is None else app
    for storage in (target.storage, target.shared):
        if storage is None:
            continue
        table_dir = storage.path(owner_sbu, owner_app, name)
        version = storage.read_text(f"{table_dir}/{_LATEST}")
        if version is not None:
            return storage.read_frame(f"{table_dir}/v={version.strip()}/data.parquet", columns)
    msg = f"Table {name!r} of app {owner_sbu}/{owner_app} has never been written. Run the job that writes it first."
    raise StoreError(msg)
