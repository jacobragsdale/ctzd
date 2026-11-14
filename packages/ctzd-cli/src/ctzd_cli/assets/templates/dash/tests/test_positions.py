"""The holdings' rules, on a few rows written here."""

import pandas as pd
import pytest

from app.logic import positions as rules

HOLDINGS = pd.DataFrame(
    {
        "book": ["Credit", "Credit", "Core Rates"],
        "cusip": ["AAA111111", "BBB222222", "CCC333333"],
        "sector": ["Industrial", "Financial", "Government"],
        "coupon": [0.05, 0.04, 0.03],
        "par": [1_000_000.0, 2_000_000.0, 4_000_000.0],
        "price": [100.0, 98.0, 101.0],
        "prior_price": [99.5, 98.0, 101.25],
        "ytw": [0.05, 0.045, 0.04],
        "duration": [4.0, 6.0, 8.0],
    }
)


def test_market_value_is_par_times_the_price_per_hundred_and_weights_sum_to_one() -> None:
    shown = rules.positions(HOLDINGS, "")
    assert shown["market_value"].tolist() == [1_000_000.0, 1_960_000.0, 4_040_000.0]
    assert shown["weight"].sum() == pytest.approx(1.0)


def test_a_book_keeps_its_own_holdings() -> None:
    assert rules.positions(HOLDINGS, "Credit")["cusip"].tolist() == ["AAA111111", "BBB222222"]


def test_the_days_pnl_is_the_price_move_plus_a_days_coupon() -> None:
    first = rules.positions(HOLDINGS, "").iloc[0]
    assert first["pnl"] == pytest.approx(5_000.0 + 1_000_000.0 * 0.05 / 365)


def test_sectors_are_largest_first_and_totals_weight_yield_by_market_value() -> None:
    shown = rules.positions(HOLDINGS, "")
    assert rules.by_sector(shown)["sector"].tolist() == ["Government", "Financial", "Industrial"]
    total = rules.totals(shown)
    assert total["market_value"] == pytest.approx(7_000_000.0)
    assert total["ytw"] == pytest.approx((0.05 * 1_000_000 + 0.045 * 1_960_000 + 0.04 * 4_040_000) / 7_000_000)


def test_an_empty_book_has_no_yield() -> None:
    assert pd.isna(rules.totals(rules.positions(HOLDINGS, "Municipals"))["ytw"])
