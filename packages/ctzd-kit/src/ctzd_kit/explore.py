"""What `ctzd sources` shows, run inside the app's environment, where the kit is installed. It prints one JSON object.

python -m ctzd_kit.explore describe              the kit's sources and loaders, each with what it holds, its
                                                 columns and an example call, and the department's skill
python -m ctzd_kit.explore tables SOURCE         the tables and views a database source's account can read
python -m ctzd_kit.explore table SOURCE TABLE    a table's columns and first rows, every number a float64
"""

import importlib
import inspect
import json
import re
import sys
from datetime import date, datetime
from typing import Final

import pandas as pd
from pandas.api import types as ptypes

from ctzd_kit.connections import MssqlSource, OracleSource, connection
from ctzd_kit.errors import KitError, SourceError
from ctzd_kit.loaders import LOADERS, Loader, sample_reason, sample_rows
from ctzd_kit.sources import Kit, Source, has_credentials, installed

SAMPLE_ROWS: Final = 5
EXAMPLE_DAY: Final = "date(2026, 11, 13)"
_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_$#]{0,127}")
_QUALIFIED = 2
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


def column_type(series: pd.Series) -> str:
    if ptypes.is_bool_dtype(series):
        return "bool"
    if ptypes.is_datetime64_any_dtype(series):
        return "date"
    if ptypes.is_numeric_dtype(series):
        return "number"
    return "text"


def example_value(parameter: inspect.Parameter) -> str:
    annotation = parameter.annotation
    if annotation in {date, "date"}:
        return EXAMPLE_DAY
    if annotation in {datetime, "datetime"}:
        return "datetime(2026, 11, 13, 7, 0)"
    if annotation in {int, "int"}:
        return "1"
    if annotation in {float, "float"}:
        return "1.0"
    if annotation in {bool, "bool"}:
        return "True"
    return repr(parameter.name)


def example_call(found: Loader) -> str:
    required = [parameter for parameter in found.signature.parameters.values() if parameter.default is inspect.Parameter.empty]
    return f"{found.source}.{found.name}(" + ", ".join(f"{parameter.name}={example_value(parameter)}" for parameter in required) + ")"


def loaders_of(source: Source) -> list[Loader]:
    importlib.import_module(source.loaders_module)
    return [found for (name, _), found in sorted(LOADERS.items()) if name == source.name and found.package == source.package]


def describe_loader(source: Source, found: Loader) -> dict[str, object]:
    try:
        rows = sample_rows(source, found.name)
        columns: list[dict[str, str]] = [{"name": str(name), "type": column_type(rows[name])} for name in rows.columns]
    except KitError as err:
        columns = [{"name": "?", "type": str(err)}]
    return {"name": found.name, "signature": f"{found.name}{found.signature}", "doc": found.doc, "columns": columns, "example": example_call(found)}


def describe_source(source: Source) -> dict[str, object]:
    try:
        reason = sample_reason(source)
    except SourceError as err:
        reason = str(err)
    return {
        "name": source.name,
        "kind": source.spec.kind,
        "description": source.spec.description,
        "refreshed": source.spec.refreshed,
        "dialect": source.spec.dialect,
        "desktop": source.spec.desktop,
        "io": list(source.spec.io),
        "credentials": has_credentials(source),
        "serves_sample": reason,
        "loaders": [describe_loader(source, found) for found in loaders_of(source)],
    }


def describe(kit: Kit) -> dict[str, object]:
    from ctzd_kit import skill  # noqa: PLC0415 - skill renders from what this module describes

    sources = [describe_source(source) for source in kit.sources.values()]
    return {
        "kit": kit.module,
        "sbu": kit.spec.sbu,
        "title": kit.spec.title,
        "sources": sources,
        "io": sorted({package for source in kit.sources.values() for package in source.spec.io}),
        "skill": skill.render(kit, sources),
    }


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


def tables(source: Source, table: str | None) -> dict[str, object]:
    """A database source's tables, or one table's columns and first rows, read with the account `ctzd env` wrote."""
    if source.spec.kind not in {"oracle", "mssql"}:
        message = f"{source.name} is a {source.spec.kind} source: it has no tables to look inside. Its loaders, in `ctzd sources`, are how an app reads it."
        return {"source": source.name, "kind": source.spec.kind, "message": message}
    config = connection(source.name)
    from ctzd_kit.drivers import mssql, oracle  # noqa: PLC0415 - the drivers load only to explore

    match config:
        case OracleSource():
            if table is None:
                return {"source": source.name, "kind": "oracle", "tables": _records(oracle.fetch(config, _ORACLE_TABLES, {}, what=f"{source.name} tables"))}
            schema, name = split_table(table)
            columns = _records(oracle.fetch(config, _ORACLE_COLUMNS, {"schema_name": schema, "name": name}, what=f"{source.name} {table} columns"))
            quoted = ".".join(f'"{part}"' for part in (schema, name) if part is not None)
            rows = oracle.fetch(config, f"SELECT * FROM {quoted} FETCH FIRST {SAMPLE_ROWS} ROWS ONLY", {}, what=f"{source.name} {table}")  # noqa: S608 - names checked against _PART
        case MssqlSource():
            if table is None:
                return {"source": source.name, "kind": "mssql", "tables": _records(mssql.fetch(config, _MSSQL_TABLES, {}, what=f"{source.name} tables"))}
            schema, name = split_table(table)
            columns = _records(mssql.fetch(config, _MSSQL_COLUMNS, {"schema_name": schema, "name": name}, what=f"{source.name} {table} columns"))
            quoted = ".".join(f"[{part}]" for part in (schema, name) if part is not None)
            rows = mssql.fetch(config, f"SELECT TOP {SAMPLE_ROWS} * FROM {quoted}", {}, what=f"{source.name} {table}")  # noqa: S608 - names checked against _PART
        case _:
            msg = f"{source.name}'s secret is for a {config.kind} source, but its source.toml says {source.spec.kind}."
            raise SourceError(msg)
    if not columns:
        msg = f"{source.name} has no table or view {table!r} that its account can read; `ctzd sources {source.name}` lists the ones it can."
        raise SourceError(msg)
    return {"source": source.name, "kind": source.spec.kind, "table": table, "columns": columns, "rows": _records(rows)}


def main(argv: list[str]) -> int:
    try:
        kit = installed()
        match argv:
            case ["describe"]:
                found = describe(kit)
            case ["tables", source]:
                found = tables(kit.source(source), None)
            case ["table", source, table]:
                found = tables(kit.source(source), table)
            case _:
                sys.stderr.write(__doc__ or "")
                return 2
    except KitError as err:
        sys.stdout.write(json.dumps({"error": str(err)}) + "\n")
        return 1
    sys.stdout.write(json.dumps(found, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
