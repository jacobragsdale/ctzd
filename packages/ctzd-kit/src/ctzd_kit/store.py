"""The table store: parquet tables a job writes and a dashboard reads, in its environment's store container.

Layout: `<root>/<sbu>/<app>/<table>/v=<version>/data.parquet`, plus a `_latest` file naming the current version. A
write lands in a new version folder and then replaces `_latest`, so a reader never sees a half-written table, and the
previous versions stay. A table written while any source served sample rows has a `_sample` file beside its data, and
reading it marks the reader's work as sample too, so a page that shows it shows the banner.

Locally the store is `.ctzd/out/store` in the app; `ctzd run JOB --local` writes there.
"""

import re
import secrets
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from ctzd_common import samples
from ctzd_kit.errors import StoreError
from ctzd_kit.frames import Storage, floats, local_path
from ctzd_kit.settings import settings

_TABLE = re.compile(r"[a-z][a-z0-9_]{0,62}")
_LATEST = "_latest"
_SAMPLE = "_sample"
KEEP_VERSIONS = 10
SAMPLE_ATTR = "ctzd_sample"


@dataclass(frozen=True)
class Target:
    storage: Storage
    sbu: str
    app: str


_override: ContextVar[Target | None] = ContextVar("ctzd_kit_store", default=None)


def storage_for(url: str, sas_token: str | None = None) -> Storage:
    """Open `abfs://container@account.dfs.core.windows.net/prefix` or a local path."""
    parts = urlsplit(url)
    if parts.scheme == "abfs":
        if parts.username is None or parts.hostname is None:
            msg = f"Store URL {url!r} must look like abfs://container@account.dfs.core.windows.net/prefix."
            raise StoreError(msg)
        return Storage.azure(account=parts.hostname.split(".", 1)[0], container_path=parts.username + parts.path, sas_token=sas_token)
    if (folder := local_path(url)) is not None:
        root = Path(folder).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return Storage.local(root.as_posix())
    msg = f"Unsupported store URL scheme {parts.scheme!r}; use abfs:// or a local path."
    raise StoreError(msg)


@contextmanager
def local(root: Path, *, sbu: str = "test", app: str = "test") -> Generator[Path]:
    """Point the store at a local folder for the block, usually a test's `tmp_path`."""
    root.mkdir(parents=True, exist_ok=True)
    token = _override.set(Target(Storage.local(root.resolve().as_posix()), sbu, app))
    try:
        yield root
    finally:
        _override.reset(token)


def _target() -> Target:
    given = _override.get()
    if given is not None:
        return given
    found = settings()
    if found.store_url is None or not found.app:
        msg = "The store needs the app's settings: run this through `ctzd run JOB --local`, `ctzd dev` or `ctzd exec`, or in a test inside ctzd_kit.testing's store."
        raise StoreError(msg)
    return Target(storage_for(found.store_url), found.sbu, found.app)


def _check_table(name: str) -> str:
    if _TABLE.fullmatch(name) is None:
        msg = f"Invalid table name {name!r}: use lowercase letters, digits and underscores, starting with a letter."
        raise StoreError(msg)
    return name


def _utc_now() -> datetime:
    return datetime.now(UTC)


def write_table(frame: pd.DataFrame, name: str, *, allow_empty: bool = False, keep: int = KEEP_VERSIONS, now: Callable[[], datetime] = _utc_now) -> str:
    """Publish `frame` as the new version of this app's table `name` and return the version's id.

    An empty frame is refused unless `allow_empty=True`, because an empty result is far more often a broken query
    than a real answer. Every number is written as a float64, so a reader gets the same type whatever wrote it.
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
    storage.write_frame(f"{version_dir}/data.parquet", floats(frame, what=f"table {name!r}"))
    if served := samples.served():
        storage.write_text(f"{version_dir}/{_SAMPLE}", ",".join(sorted(served)))
    pointer = f"{table_dir}/{_LATEST}.{secrets.token_hex(4)}.tmp"
    storage.write_text(pointer, version)
    storage.move(pointer, f"{table_dir}/{_LATEST}")
    versions = [entry for entry in storage.child_dirs(table_dir) if entry.startswith("v=")]
    for old in versions[:-keep]:
        storage.delete_dir(f"{table_dir}/{old}")
    return version


def read_table(name: str, *, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """The latest version of this app's table `name`."""
    _check_table(name)
    target = _target()
    table_dir = target.storage.path(target.sbu, target.app, name)
    version = target.storage.read_text(f"{table_dir}/{_LATEST}")
    if version is None:
        msg = f"Table {name!r} has never been written here. Run the job that writes it first: `ctzd run JOB --local`."
        raise StoreError(msg)
    version_dir = f"{table_dir}/v={version.strip()}"
    frame = floats(target.storage.read_frame(f"{version_dir}/data.parquet", columns), what=f"table {name!r}")
    marked = target.storage.read_text(f"{version_dir}/{_SAMPLE}")
    if marked is not None:
        for source in marked.split(","):
            samples.mark(source)
        frame.attrs[SAMPLE_ATTR] = True
    return frame
