"""The seam: `@loader`, the one way an app reads a kit's data, and `query`, which a loader or an app runs SQL through.

A loader is a plain function in `sources/NAME/loaders.py` that returns a DataFrame. Calling it does one of two things:

- **serves its sample rows**, `sources/NAME/fixtures/LOADER.csv` (or the app's own `fixtures/NAME/LOADER.csv`, which
  wins), without running anything underneath: for `sample`, which is only sample rows; with every source faked
  (`ctzd check`, `ctzd dev --fake`, tests); for a source that cannot run on a desktop, there; and for a source whose
  secret has not reached the process, outside prod. A page then shows the sample-data banner, and a table a job writes
  carries the mark (`ctzd_common.samples`).
- **runs the function**, with at most the source's `max_concurrent` reads at once in the process, and hands the app its
  frame with every number a float64 and every id (`id` or `..._id`) text.
"""

import functools
import inspect
import threading
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

import pandas as pd
from pandas.api import types as ptypes

from ctzd_common import samples
from ctzd_kit import fixtures
from ctzd_kit.errors import DataError, SourceError
from ctzd_kit.settings import settings
from ctzd_kit.sources import SQL_KINDS, Source, has_credentials, installed, source_of

_running: ContextVar[Source | None] = ContextVar("ctzd_kit_running", default=None)
_limits: dict[str, threading.BoundedSemaphore] = {}
_limits_lock = threading.Lock()


@dataclass(frozen=True)
class Loader:
    source: str
    name: str
    package: str
    function: Callable[..., pd.DataFrame]
    doc: str
    signature: inspect.Signature


LOADERS: dict[tuple[str, str], Loader] = {}
"""Every loader imported in this process, by source and name."""


def sample_reason(source: Source) -> str | None:
    """Why `source` serves its sample rows here, or None when it is read for real."""
    found = settings()
    if source.spec.kind == "sample":
        return "it exists only as sample rows"
    if found.fake:
        return "every source serves sample rows here"
    if found.env == "local" and not source.spec.desktop:
        return "it cannot run on a desktop"
    if not has_credentials(source):
        if found.env == "prod":
            msg = f"Source {source.name} has no credentials in prod, so the app cannot read it. Ask the platform team to add its secret to the prod Key Vault."
            raise SourceError(msg)
        return "it has no credentials here"
    return None


def loader[**P](function: Callable[P, pd.DataFrame]) -> Callable[P, pd.DataFrame]:
    """Make `function`, in `sources/NAME/loaders.py`, a loader of source NAME."""
    package = function.__module__.rpartition(".")[0]
    source = source_of(package)
    doc = inspect.getdoc(function) or ""
    name = str(getattr(function, "__name__", "loader"))

    @functools.wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> pd.DataFrame:
        if sample_reason(source) is not None:
            samples.mark(source.name)
            return sample_rows(source, name)
        with limited(source), running(source):
            frame = function(*args, **kwargs)
        return checked(frame, f"{source.name}.{name}")

    LOADERS[(source.name, name)] = Loader(source.name, name, package, wrapper, doc, inspect.signature(function))
    return wrapper


@contextmanager
def running(source: Source) -> Generator[None]:
    token = _running.set(source)
    try:
        yield
    finally:
        _running.reset(token)


@contextmanager
def limited(source: Source) -> Generator[None]:
    """At most the source's `max_concurrent` reads at once in this process, a dashboard's users and a job's threads
    alike."""
    with _limits_lock:
        gate = _limits.setdefault(source.name, threading.BoundedSemaphore(source.spec.max_concurrent))
    with gate:
        yield


def current() -> Source:
    found = _running.get()
    if found is None:
        msg = "This is for a kit's loaders: call it inside a function decorated with @loader in sources/NAME/loaders.py."
        raise SourceError(msg)
    return found


def checked(frame: object, what: str) -> pd.DataFrame:
    """A loader's frame as the app gets it: every number a float64, every id text."""
    if not isinstance(frame, pd.DataFrame):
        msg = f"{what} returned {type(frame).__name__}; a loader returns a pandas DataFrame."
        raise DataError(msg)
    from ctzd_kit.frames import floats  # noqa: PLC0415 - pyarrow loads with the first real read

    found = floats(frame, what=what)
    numeric_ids = [str(column) for column in found.columns if fixtures.is_id(str(column)) and ptypes.is_numeric_dtype(found[column])]
    if numeric_ids:
        msg = f"{what}: {', '.join(numeric_ids)} hold numbers; an id is text. Select it as text, or convert it with .astype('string')."
        raise DataError(msg)
    return found


def sample_rows(source: Source, name: str) -> pd.DataFrame:
    """The sample rows of `source`'s loader or SQL file `name`: the app's own, `fixtures/SOURCE/NAME.csv`, else the
    kit's."""
    own = settings().fixtures
    if own is not None and (own / source.name / f"{name}.csv").is_file():
        return fixtures.read(own / source.name / f"{name}.csv")
    kit = source.folder / "fixtures" / f"{name}.csv"
    if kit.is_file():
        return fixtures.read_text(kit.read_text(encoding="utf-8"))
    msg = f"{source.name} has no sample rows for {name}: write a few made-up rows to fixtures/{source.name}/{name}.csv, one column per column the real read returns."
    raise SourceError(msg)


def _read_sql(folder: Traversable, sql: str) -> str:
    path = folder / "sql" / sql
    if not path.is_file():
        msg = f"There is no SQL file sql/{sql}."
        raise SourceError(msg)
    return path.read_text(encoding="utf-8")


def _run_sql(source: Source, text: str, binds: Mapping[str, object], what: str) -> pd.DataFrame:
    from ctzd_kit.connections import MssqlSource, OracleSource, connection  # noqa: PLC0415 - pydantic models, loaded on a real read

    config = connection(source.name)
    if isinstance(config, OracleSource):
        from ctzd_kit.drivers import oracle  # noqa: PLC0415 - the driver loads with its first query

        return oracle.fetch(config, text, binds, what=what)
    if isinstance(config, MssqlSource):
        from ctzd_kit.drivers import mssql  # noqa: PLC0415 - the driver loads with its first query

        return mssql.fetch(config, text, binds, what=what)
    msg = f"{source.name} is a {config.kind} source, which runs no SQL."
    raise SourceError(msg)


def query(sql: str, /, **binds: object) -> pd.DataFrame:
    """Inside a loader: run its source's `sql/SQL` file with `binds` and return the rows. Bind by name: `:name` for
    Oracle, `%(name)s` for SQL Server."""
    source = current()
    return checked(_run_sql(source, _read_sql(source.folder, sql), binds, f"{source.name} {sql}"), f"{source.name} {sql}")


def app_query(source_name: str, sql: str, /, **binds: object) -> pd.DataFrame:
    """For an app's `data/`: run its own `src/app/data/sql/SQL` file against the kit's source `source_name`. With
    sample rows served, the rows are the app's `fixtures/SOURCE/STEM.csv`."""
    source = installed().source(source_name)
    if source.spec.kind not in SQL_KINDS:
        msg = f"{source_name} is a {source.spec.kind} source, which runs no SQL: read it through its loaders, as `ctzd sources {source_name}` lists them."
        raise SourceError(msg)
    if sample_reason(source) is not None:
        samples.mark(source.name)
        return sample_rows(source, Path(sql).stem)
    with limited(source):
        frame = _run_sql(source, _read_sql(resources.files("app.data"), sql), binds, f"{source_name} {sql}")
    return checked(frame, f"{source_name} {sql}")
