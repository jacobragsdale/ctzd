"""Every number an app sees is a float64, from a driver's Arrow and from any other frame alike."""

import re
from decimal import Decimal

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from ctzd_kit import DataError
from ctzd_kit.frames import EXACT_LIMIT, floats, to_pandas


def test_decimals_become_the_nearest_float() -> None:
    table = pa.table({"price": pa.array([Decimal("2.675"), Decimal("0.1"), Decimal("99.84375"), None], pa.decimal128(38, 10))})
    prices = to_pandas(table)
    assert prices["price"].dtype == "float64"
    assert prices["price"].tolist()[:3] == [2.675, 0.1, 99.84375]
    assert np.isnan(prices["price"].iloc[3])


def test_integers_become_floats_with_nan_for_missing_and_booleans_stay() -> None:
    counts = to_pandas(pa.table({"bonds": pa.array([3, None, 12], pa.int64()), "flag": pa.array([True, False, True])}))
    assert counts["bonds"].dtype == "float64"
    assert counts["bonds"].tolist()[0::2] == [3.0, 12.0]
    assert np.isnan(counts["bonds"].iloc[1])
    assert counts["flag"].dtype == "bool"


def test_single_precision_floats_keep_their_shortest_text() -> None:
    assert to_pandas(pa.table({"rate": pa.array([0.1, 1.5], pa.float32())}))["rate"].tolist() == [0.1, 1.5]


def test_dates_become_datetimes() -> None:
    import datetime as dt  # noqa: PLC0415 - only this test builds dates

    found = to_pandas(pa.table({"as_of": pa.array([dt.date(2026, 11, 13)], pa.date32())}))
    assert found["as_of"].dtype.kind == "M"


def test_whole_numbers_beyond_two_to_the_53_are_refused_with_the_fix() -> None:
    with pytest.raises(DataError) as caught:
        to_pandas(pa.table({"trade_id": pa.array([1, EXACT_LIMIT + 1], pa.int64())}), what="fi_trades trades.sql")
    message = str(caught.value)
    assert message.startswith("fi_trades trades.sql: column 'trade_id'")
    assert "TO_CHAR(trade_id)" in message
    assert "CAST(trade_id AS VARCHAR(40))" in message


@pytest.mark.parametrize("value", [EXACT_LIMIT + 1, -EXACT_LIMIT - 1])
def test_whole_decimals_beyond_two_to_the_53_are_refused(value: int) -> None:
    with pytest.raises(DataError, match=re.escape("'account'")):
        to_pandas(pa.table({"account": pa.array([Decimal(value)], pa.decimal128(20, 0))}))


def test_two_to_the_53_itself_is_exact() -> None:
    assert to_pandas(pa.table({"n": pa.array([EXACT_LIMIT], pa.int64())}))["n"].iloc[0] == float(EXACT_LIMIT)


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
    assert {str(name): str(dtype) for name, dtype in converted.dtypes.items()} == {
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


def test_floats_refuses_a_frame_whose_whole_numbers_a_float_cannot_hold() -> None:
    with pytest.raises(DataError, match="'quantity'"):
        floats(pd.DataFrame({"quantity": [EXACT_LIMIT + 1]}), what="frame")


def test_floats_returns_a_frame_of_floats_unchanged() -> None:
    frame = pd.DataFrame({"price": [1.5, 2.5], "isin": ["X1", "X2"]})
    assert floats(frame, what="frame") is frame


def test_floats_keeps_duplicate_column_names_in_place() -> None:
    frame = pd.DataFrame([[1, "a", 2]], columns=["n", "s", "n"])
    converted = floats(frame, what="frame")
    assert converted.columns.tolist() == ["n", "s", "n"]
    assert converted.iloc[0].tolist() == [1.0, "a", 2.0]
