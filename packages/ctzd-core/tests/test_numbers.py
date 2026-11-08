"""Every number an app sees is a float64: from the drivers' Arrow, fakes, REST responses, and the store."""

import re
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

import ctzd
from ctzd._arrow import EXACT_LIMIT, floats, to_pandas
from ctzd.rest import records_frame


def test_decimals_become_the_nearest_float() -> None:
    table = pa.table({"price": pa.array([Decimal("2.675"), Decimal("0.1"), Decimal("99.84375"), None], pa.decimal128(38, 10))})
    prices = to_pandas(table)
    assert prices["price"].dtype == "float64"
    assert prices["price"].tolist()[:3] == [2.675, 0.1, 99.84375]
    assert np.isnan(prices["price"].iloc[3])


def test_integers_become_floats_with_nan_for_missing() -> None:
    table = pa.table({"bonds": pa.array([3, None, 12], pa.int64()), "flag": pa.array([True, False, True])})
    counts = to_pandas(table)
    assert counts["bonds"].dtype == "float64"
    assert counts["bonds"].tolist()[0::2] == [3.0, 12.0]
    assert np.isnan(counts["bonds"].iloc[1])
    assert counts["flag"].dtype == "bool"


def test_single_precision_floats_keep_their_shortest_text() -> None:
    table = pa.table({"rate": pa.array([0.1, 1.5], pa.float32())})
    assert to_pandas(table)["rate"].tolist() == [0.1, 1.5]


def test_whole_numbers_beyond_two_to_the_53_are_refused_with_the_fix() -> None:
    table = pa.table({"trade_id": pa.array([1, EXACT_LIMIT + 1], pa.int64())})
    with pytest.raises(ctzd.DataError) as caught:
        to_pandas(table, what="fi_trades sql/trades.sql")
    message = str(caught.value)
    assert message.startswith("fi_trades sql/trades.sql: column 'trade_id'")
    assert "TO_CHAR(trade_id)" in message
    assert "CAST(trade_id AS VARCHAR(40))" in message


def test_whole_decimals_beyond_two_to_the_53_are_refused() -> None:
    table = pa.table({"account": pa.array([Decimal(EXACT_LIMIT + 1)], pa.decimal128(20, 0))})
    with pytest.raises(ctzd.DataError, match="'account'"):
        to_pandas(table)


def test_large_amounts_with_decimal_places_are_not_refused() -> None:
    table = pa.table({"notional": pa.array([Decimal("12345678901234567.25")], pa.decimal128(38, 2))})
    assert to_pandas(table)["notional"].iloc[0] == pytest.approx(1.2345678901234568e16)


def test_floats_converts_every_kind_of_integer_and_float_column() -> None:
    frame = pd.DataFrame(
        {
            "numpy_int": np.array([1, 2], dtype="int64"),
            "nullable_int": pd.array([1, None], dtype="Int64"),
            "arrow_int": pd.Series([1, None], dtype=pd.ArrowDtype(pa.int64())),
            "single": np.array([0.1, 0.2], dtype="float32"),
            "nullable_float": pd.array([0.5, None], dtype="Float64"),
            "isin": ["X1", "X2"],
            "flag": [True, False],
        }
    )
    converted = floats(frame, what="frame")
    assert {name: str(dtype) for name, dtype in converted.dtypes.items()} == {
        "numpy_int": "float64",
        "nullable_int": "float64",
        "arrow_int": "float64",
        "single": "float64",
        "nullable_float": "float64",
        "isin": str(frame["isin"].dtype),
        "flag": "bool",
    }
    assert converted["single"].tolist() == [0.1, 0.2]
    assert np.isnan(converted["nullable_int"].iloc[1])
    assert frame["numpy_int"].dtype == "int64", "the caller's frame is left as it was"


def test_floats_converts_arrow_backed_decimals_through_their_text() -> None:
    frame = pd.DataFrame({"price": pd.Series([Decimal("2.675"), None], dtype=pd.ArrowDtype(pa.decimal128(10, 3)))})
    converted = floats(frame, what="frame")
    assert converted["price"].dtype == "float64"
    assert converted["price"].iloc[0] == 2.675


def test_floats_returns_a_frame_of_floats_unchanged() -> None:
    frame = pd.DataFrame({"price": [1.5, 2.5], "isin": ["X1", "X2"]})
    assert floats(frame, what="frame") is frame


def test_a_fake_query_answers_floats_as_a_real_source_does() -> None:
    trades = pd.DataFrame({"isin": ["X1"], "quantity": [5_000_000]})
    with ctzd.testing.fake_sources(fi_trades={"sql/trades.sql": trades}):
        assert ctzd.query("fi_trades", "sql/trades.sql")["quantity"].dtype == "float64"


def test_a_fake_query_refuses_ids_beyond_two_to_the_53() -> None:
    trades = pd.DataFrame({"trade_id": [EXACT_LIMIT + 2]})
    with ctzd.testing.fake_sources(fi_trades={"sql/trades.sql": trades}), pytest.raises(ctzd.DataError, match=re.escape("fake fi_trades sql/trades.sql")):
        ctzd.query("fi_trades", "sql/trades.sql")


def test_rest_rows_answer_floats() -> None:
    bonds = records_frame([{"isin": "X1", "rating_rank": 3}, {"isin": "X2", "rating_rank": 7}], None)
    assert bonds["rating_rank"].dtype == "float64"


def test_the_store_writes_and_reads_floats(tmp_path: Path) -> None:
    with ctzd.testing.fake_store(tmp_path):
        ctzd.write_table(pd.DataFrame({"sector": ["Banks"], "bonds": [42]}), "sector_summary")
        summary = ctzd.read_table("sector_summary")
    assert summary["bonds"].dtype == "float64"
    assert summary["bonds"].tolist() == [42.0]
