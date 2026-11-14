"""Sample rows come back with the types the real read brings."""

import pandas as pd

from ctzd_kit import fixtures


def test_each_column_is_typed_as_the_real_read_types_it() -> None:
    rows = fixtures.read_text(
        "trade_id,account,sedol,trade_date,booked_at,closed,quantity,note,price\n"
        "1,00123456,0263494,2026-11-12,2026-11-12T09:31:00,true,100,first,1e2\n"
        "2,00123457,0263495,2026-11-13,2026-11-13 10:15,False,-2.5,,.5\n"
    )
    assert rows["trade_id"].tolist() == ["1", "2"], "an id stays text"
    assert rows["account"].tolist() == ["00123456", "00123457"], "a leading zero stays text"
    assert rows["sedol"].iloc[0] == "0263494"
    assert rows["trade_date"].tolist() == [pd.Timestamp("2026-11-12"), pd.Timestamp("2026-11-13")]
    assert rows["booked_at"].iloc[1] == pd.Timestamp("2026-11-13 10:15")
    assert rows["closed"].dtype == "boolean"
    assert rows["closed"].tolist() == [True, False]
    assert rows["quantity"].dtype == "float64"
    assert rows["quantity"].tolist() == [100.0, -2.5]
    assert rows["price"].tolist() == [100.0, 0.5]
    assert rows["note"].iloc[0] == "first"
    assert pd.isna(rows["note"].iloc[1]), "an empty cell is missing"


def test_a_column_that_mixes_kinds_stays_text() -> None:
    rows = fixtures.read_text("rating,maturity,flag\nAA,2026-11-12,true\n3,soon,maybe\n")
    assert rows["rating"].tolist() == ["AA", "3"]
    assert rows["maturity"].tolist() == ["2026-11-12", "soon"]
    assert rows["flag"].tolist() == ["true", "maybe"]


def test_missing_cells_stay_missing_in_every_type() -> None:
    rows = fixtures.read_text("price,closed,as_of,empty\n1.5,true,2026-11-12,\n,,,\n")
    assert pd.isna(rows["price"].iloc[1])
    assert pd.isna(rows["closed"].iloc[1])
    assert pd.isna(rows["as_of"].iloc[1])
    assert rows["empty"].isna().all()


def test_ids_are_named_id_or_end_in_id() -> None:
    assert fixtures.is_id("id")
    assert fixtures.is_id("account_id")
    assert not fixtures.is_id("idea")
    assert not fixtures.is_id("valid")
