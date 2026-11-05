"""Tier 4: ctzd.query against real Oracle and SQL Server. Run with scripts/driver-spike.sh."""

import os
import time
from pathlib import Path

import mssql_python
import oracledb
import pandas as pd
import pytest

import ctzd
from ctzd._mssql import connection_string
from ctzd.sources import MssqlSource, OracleSource, load_source

pytestmark = pytest.mark.integration
ROWS = 200_000


def _source[T](name: str, kind: type[T]) -> T:
    source = load_source(name, os.environ)
    assert isinstance(source, kind), f"set CTZD_SOURCE_{name.upper()} to a {kind.__name__} description"
    return source


@pytest.fixture(scope="module")
def oracle_table() -> None:
    source = _source("spike_oracle", OracleSource)
    with oracledb.connect(user=source.user, password=source.password.get_secret_value(), dsn=source.dsn) as connection, connection.cursor() as cursor:
        cursor.execute("begin execute immediate 'drop table ctzd_prices'; exception when others then null; end;")
        cursor.execute("create table ctzd_prices (isin varchar2(12), price number(12, 4), qty number(10), as_of date, note varchar2(20))")
        cursor.execute(
            """insert /*+ append */ into ctzd_prices
                select 'XS' || lpad(level, 10, '0'), level / 100, mod(level, 1000), date '2026-11-04' - mod(level, 30),
                       case when mod(level, 10) = 0 then null else 'n' || level end
                from dual connect by level <= :row_count""",
            {"row_count": ROWS},
        )
        connection.commit()


@pytest.fixture(scope="module")
def mssql_table() -> None:
    source = _source("spike_mssql", MssqlSource)
    with mssql_python.connect(connection_string(source)) as connection, connection.cursor() as cursor:
        cursor.execute("drop table if exists dbo.ctzd_prices")
        cursor.execute("create table dbo.ctzd_prices (isin varchar(12), price decimal(12, 4), qty int, as_of date, note varchar(20))")
        cursor.execute(
            """insert into dbo.ctzd_prices
                select 'XS' + right('0000000000' + cast(value as varchar(10)), 10), value / 100.0, value %% 1000,
                       dateadd(day, -(value %% 30), '2026-11-04'), case when value %% 10 = 0 then null else 'n' + cast(value as varchar(10)) end
                from generate_series(1, %(rows)s)""",
            {"rows": ROWS},
        )
        connection.commit()


def _sql(tmp_path: Path, text: str) -> str:
    path = tmp_path / "query.sql"
    path.write_text(text)
    return str(path)


def _check_types(frame: pd.DataFrame) -> None:
    assert pd.api.types.is_string_dtype(frame["isin"])
    assert frame["price"].dtype == "float64"  # decimals arrive as floats
    assert pd.api.types.is_numeric_dtype(frame["qty"])
    assert pd.api.types.is_datetime64_any_dtype(frame["as_of"])
    assert frame["note"].isna().any()


@pytest.mark.usefixtures("oracle_table")
def test_oracle_rows_arrive_typed_and_fast(tmp_path: Path) -> None:
    started = time.perf_counter()
    frame = ctzd.query("spike_oracle", _sql(tmp_path, "select isin, price, qty, as_of, note from ctzd_prices"))
    elapsed = time.perf_counter() - started
    assert len(frame) == ROWS
    _check_types(frame)
    print(f"oracle: {ROWS} rows in {elapsed:.2f} s; dtypes {dict(frame.dtypes.astype(str))}")  # noqa: T201 - the spike's measurement.


@pytest.mark.usefixtures("oracle_table")
def test_oracle_binds_by_name(tmp_path: Path) -> None:
    frame = ctzd.query("spike_oracle", _sql(tmp_path, "select isin from ctzd_prices where qty = :qty and as_of = :as_of"), {"qty": 7, "as_of": pd.Timestamp("2026-10-28").to_pydatetime()})
    assert len(frame) > 0


@pytest.mark.usefixtures("mssql_table")
def test_mssql_rows_arrive_typed_and_fast(tmp_path: Path) -> None:
    started = time.perf_counter()
    frame = ctzd.query("spike_mssql", _sql(tmp_path, "select isin, price, qty, as_of, note from dbo.ctzd_prices"))
    elapsed = time.perf_counter() - started
    assert len(frame) == ROWS
    _check_types(frame)
    print(f"mssql: {ROWS} rows in {elapsed:.2f} s; dtypes {dict(frame.dtypes.astype(str))}")  # noqa: T201 - the spike's measurement.


@pytest.mark.usefixtures("mssql_table")
def test_mssql_binds_by_name(tmp_path: Path) -> None:
    frame = ctzd.query("spike_mssql", _sql(tmp_path, "select isin from dbo.ctzd_prices where qty = %(qty)s"), {"qty": 7})
    assert len(frame) == ROWS // 1000
