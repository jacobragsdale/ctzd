"""`ctzd.query`: run a SQL file against a named Oracle or SQL Server source."""

import logging
import os
import time
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import assert_never

import pandas as pd

from ctzd import _arrow, _mssql, _oracle, _overrides
from ctzd._errors import SourceError
from ctzd.sources import MssqlSource, OracleSource, RestSource, TeamsSource, check_name, load_source

_LOG = logging.getLogger("ctzd.query")
SLOW_SECONDS = 3.0
SQL_PACKAGE = "app.data"


def read_sql(sql: str) -> str:
    """Read a SQL file: absolute paths as given, relative paths from the app's `src/app/data/` folder."""
    path = Path(sql)
    if path.is_absolute():
        return path.read_text(encoding="utf-8")
    try:
        return resources.files(SQL_PACKAGE).joinpath(sql).read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError) as err:
        msg = f"SQL file {sql!r} not found under src/app/data/."
        raise SourceError(msg) from err


def query(source: str, sql: str, params: Mapping[str, object] | None = None) -> pd.DataFrame:
    """Run the SQL file `sql` against `source` and return the rows as a DataFrame.

    Bind parameters by name: `:name` for Oracle, `%(name)s` for SQL Server. Every
    number arrives as a float64, NaN where it is missing; select ids as text.
    """
    check_name(source)
    bound = dict(params or {})
    started = time.perf_counter()
    fakes = _overrides.current().sql
    frame = _fake_query(fakes, source, sql, bound) if fakes is not None else _real_query(source, sql, bound)
    elapsed = time.perf_counter() - started
    if elapsed > SLOW_SECONDS:
        _LOG.warning("Slow query: %s %s took %.1f s for %d rows. If a dashboard waits on it, move it into a job.", source, sql, elapsed, len(frame))
    else:
        _LOG.info("query %s %s: %d rows in %.2f s", source, sql, len(frame), elapsed)
    return frame


def _real_query(source: str, sql: str, params: Mapping[str, object]) -> pd.DataFrame:
    config = load_source(source, os.environ)
    text = read_sql(sql)
    match config:
        case OracleSource():
            return _oracle.fetch(config, text, params, what=f"{source} {sql}")
        case MssqlSource():
            return _mssql.fetch(config, text, params, what=f"{source} {sql}")
        case RestSource() | TeamsSource():
            msg = f"Source {source!r} is a {config.kind} source; call ctzd.rest({source!r}) instead of ctzd.query."
            raise SourceError(msg)
        case _:
            assert_never(config)


def _fake_query(fakes: Mapping[str, Mapping[str, object]], source: str, sql: str, params: Mapping[str, object]) -> pd.DataFrame:
    routes = fakes.get(source)
    if routes is None:
        msg = f"ctzd.testing.fake_sources has no fake for source {source!r}; faked sources: {sorted(fakes)}."
        raise SourceError(msg)
    result = routes.get(sql)
    if result is None:
        msg = f"Fake source {source!r} has no result for {sql!r}; known SQL files: {sorted(routes)}."
        raise SourceError(msg)
    # A fake's numbers become float64 as a real source's do, so a test sees what the app will.
    if isinstance(result, pd.DataFrame):
        return _arrow.floats(result.copy(), what=f"fake {source} {sql}")
    if isinstance(result, _overrides.SqlHandler):
        return _arrow.floats(result.function(params), what=f"fake {source} {sql}")
    msg = f"Fake for {source!r} {sql!r} must be a DataFrame or ctzd.testing.by_params(function)."
    raise SourceError(msg)
