"""Oracle gateway: python-oracledb in thin mode, which needs no Oracle client libraries."""

from collections.abc import Mapping

import oracledb
import pandas as pd

from ctzd._arrow import to_pandas
from ctzd.sources import OracleSource

# Rows per network round trip. oracledb's default of 100 suits OLTP; analytics reads are large.
_ARRAYSIZE = 10_000
_CONNECT_TIMEOUT_SECONDS = 15.0
_CALL_TIMEOUT_MS = 10 * 60 * 1000  # per round trip


def _column_name(name: str) -> str:
    """Oracle reports unquoted identifiers in upper case; lower-case those, as SQLAlchemy does. Quoted mixed-case names stay."""
    return name.lower() if name.isupper() else name


def fetch(source: OracleSource, sql: str, params: Mapping[str, object], *, what: str) -> pd.DataFrame:
    """The rows, every number a float64. `NUMBER(p,s)` arrives as an exact decimal and becomes the nearest float64; an
    unconstrained `NUMBER` arrives as a float64 from the driver."""
    with oracledb.connect(user=source.user, password=source.password.get_secret_value(), dsn=source.dsn, tcp_connect_timeout=_CONNECT_TIMEOUT_SECONDS) as connection:
        connection.call_timeout = _CALL_TIMEOUT_MS
        fetched = connection.fetch_df_all(statement=sql, parameters=dict(params) or None, arraysize=_ARRAYSIZE, fetch_decimals=True)
        frame = to_pandas(fetched, what=what)
    return frame.rename(columns=_column_name)
