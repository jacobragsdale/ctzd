"""Look at a database source before writing a loader: what the account can read, a table's columns, and a few rows.

    python -m ctzd.explore SOURCE          the tables and views the source's account can read
    python -m ctzd.explore SOURCE TABLE    TABLE's columns and its first rows; TABLE as the first form lists it

`ctzd sources SOURCE [TABLE]` runs it in the app's environment, with the credentials `ctzd env` wrote. It prints one
JSON object. Every number in the rows is a float64, as `ctzd.query` returns it.
"""

import json
import os
import re
import sys
from typing import assert_never

import pandas as pd

from ctzd import _mssql, _oracle
from ctzd._errors import CtzdError, SourceError
from ctzd.sources import MssqlSource, OracleSource, RestSource, TeamsSource, load_source

SAMPLE_ROWS = 5
MAX_TABLES = 500  # as the two listing queries below say
_QUALIFIED = 2  # SCHEMA.TABLE
_ARGUMENTS = (1, 2)  # SOURCE and an optional TABLE
_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_$#]{0,127}")
# Oracle's own schemas (SYS, XDB, MDSYS, ...) are marked oracle_maintained; the rest are the source's data.
_ORACLE_TABLES = """
SELECT owner AS schema_name, table_name AS name, 'table' AS kind FROM all_tables
WHERE owner IN (SELECT username FROM all_users WHERE oracle_maintained = 'N')
UNION ALL
SELECT owner, view_name, 'view' FROM all_views
WHERE owner IN (SELECT username FROM all_users WHERE oracle_maintained = 'N')
ORDER BY 1, 2
FETCH FIRST 500 ROWS ONLY
"""
_ORACLE_COLUMNS = """
SELECT column_name AS name, data_type AS type, nullable FROM all_tab_columns
WHERE owner = COALESCE(:schema_name, SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')) AND table_name = :name
ORDER BY column_id
"""
_MSSQL_TABLES = """
SELECT TOP 500 TABLE_SCHEMA AS schema_name, TABLE_NAME AS name, LOWER(REPLACE(TABLE_TYPE, 'BASE ', '')) AS kind
FROM INFORMATION_SCHEMA.TABLES ORDER BY TABLE_SCHEMA, TABLE_NAME
"""
_MSSQL_COLUMNS = """
SELECT COLUMN_NAME AS name, DATA_TYPE AS type, IS_NULLABLE AS nullable FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = COALESCE(%(schema_name)s, SCHEMA_NAME()) AND TABLE_NAME = %(name)s
ORDER BY ORDINAL_POSITION
"""


def split_table(table: str) -> tuple[str | None, str]:
    """`SCHEMA.TABLE` or `TABLE`, each part a plain identifier: the one guard between a name and the SQL it goes into."""
    parts = table.split(".")
    if len(parts) > _QUALIFIED or not all(_PART.fullmatch(part) for part in parts):
        msg = f"{table!r} is not a table name; give it as `ctzd sources SOURCE` lists it, such as FI.BOND_PRICES or dbo.trades."
        raise SourceError(msg)
    return (None, parts[0]) if len(parts) == 1 else (parts[0], parts[1])


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    loaded: object = json.loads(frame.to_json(orient="records", date_format="iso"))
    return [{str(key): value for key, value in row.items()} for row in loaded if isinstance(row, dict)] if isinstance(loaded, list) else []


def explore(source: str, table: str | None = None) -> dict[str, object]:
    """What `python -m ctzd.explore` prints, as a dict."""
    config = load_source(source, os.environ)
    match config:
        case OracleSource():
            if table is None:
                return {"source": source, "kind": "oracle", "tables": _records(_oracle.fetch(config, _ORACLE_TABLES, {}, what=f"{source} tables"))}
            schema, name = split_table(table)
            columns = _records(_oracle.fetch(config, _ORACLE_COLUMNS, {"schema_name": schema, "name": name}, what=f"{source} {table} columns"))
            quoted = ".".join(f'"{part}"' for part in (schema, name) if part is not None)
            rows = _oracle.fetch(config, f"SELECT * FROM {quoted} FETCH FIRST {SAMPLE_ROWS} ROWS ONLY", {}, what=f"{source} {table}")  # noqa: S608 - names checked against _PART
        case MssqlSource():
            if table is None:
                return {"source": source, "kind": "mssql", "tables": _records(_mssql.fetch(config, _MSSQL_TABLES, {}, what=f"{source} tables"))}
            schema, name = split_table(table)
            columns = _records(_mssql.fetch(config, _MSSQL_COLUMNS, {"schema_name": schema, "name": name}, what=f"{source} {table} columns"))
            quoted = ".".join(f"[{part}]" for part in (schema, name) if part is not None)
            rows = _mssql.fetch(config, f"SELECT TOP {SAMPLE_ROWS} * FROM {quoted}", {}, what=f"{source} {table}")  # noqa: S608 - names checked against _PART
        case RestSource() | TeamsSource():
            message = f"{source} is a {config.kind} source: it has no tables. Its description in `ctzd sources` says what it serves; read it with ctzd.rest."
            return {"source": source, "kind": config.kind, "description": config.description, "message": message}
        case _:
            assert_never(config)
    if not columns:
        msg = f"{source} has no table or view {table!r} that its account can read; `ctzd sources {source}` lists the ones it can."
        raise SourceError(msg)
    return {"source": source, "kind": config.kind, "table": table, "columns": columns, "rows": _records(rows)}


def main(argv: list[str]) -> int:
    if len(argv) not in _ARGUMENTS:
        sys.stderr.write("usage: python -m ctzd.explore SOURCE [TABLE]\n")
        return 2
    try:
        found = explore(argv[0], argv[1] if len(argv) > 1 else None)
    except CtzdError as err:
        sys.stdout.write(json.dumps({"error": str(err)}) + "\n")
        return 1
    sys.stdout.write(json.dumps(found, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
