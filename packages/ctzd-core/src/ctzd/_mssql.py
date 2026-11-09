"""SQL Server gateway: Microsoft's mssql-python, which bundles its own ODBC driver."""

from collections.abc import Mapping

import mssql_python
import pandas as pd

from ctzd._arrow import to_pandas
from ctzd.settings import session_name
from ctzd.sources import MssqlSource

_LOGIN_TIMEOUT_SECONDS = 30
_QUERY_TIMEOUT_SECONDS = 10 * 60  # as Oracle's call timeout: a dashboard query that runs longer belongs in a job
# mssql-python fixes the session's program name to its own, so the app's name goes into CONTEXT_INFO, which
# sys.dm_exec_sessions shows next to the session; a read-only login may set it.
_NAME_SESSION = "DECLARE @ctzd_app varbinary(128) = CAST(CAST(%(app)s AS varchar(128)) AS varbinary(128)); SET CONTEXT_INFO @ctzd_app"
_NEEDS_QUOTING = frozenset(";{}=")


def _value(text: str) -> str:
    if text != text.strip() or any(char in _NEEDS_QUOTING for char in text):
        return "{" + text.replace("}", "}}") + "}"
    return text


def connection_string(source: MssqlSource) -> str:
    parts = {
        "Server": source.server,
        "Database": source.database,
        "Uid": source.user,
        "Pwd": source.password.get_secret_value(),
        "Encrypt": "yes" if source.encrypt else "no",
        "TrustServerCertificate": "yes" if source.trust_server_certificate else "no",
    }
    return ";".join(f"{key}={_value(value)}" for key, value in parts.items())


def fetch(source: MssqlSource, sql: str, params: Mapping[str, object], *, what: str) -> pd.DataFrame:
    """The rows, every number a float64."""
    with mssql_python.connect(connection_string(source), timeout=_LOGIN_TIMEOUT_SECONDS) as connection:
        connection.timeout = _QUERY_TIMEOUT_SECONDS
        with connection.cursor() as cursor:
            cursor.execute(_NAME_SESSION, {"app": session_name()})
            return _run(cursor, sql, params, what=what)


def _run(cursor: mssql_python.Cursor, sql: str, params: Mapping[str, object], *, what: str) -> pd.DataFrame:
    if params:
        cursor.execute(sql, dict(params))
    else:
        cursor.execute(sql)
    return to_pandas(cursor.arrow(), what=what)
