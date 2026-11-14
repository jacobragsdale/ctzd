"""FI's loaders answer their sample rows on the fakes, and its helpers count days as its bonds do."""

from datetime import date

import pytest

from ctzd_fi import fi_curves, fi_pricing, fi_risk
from ctzd_fi.helpers.daycount import Basis, year_fraction
from ctzd_kit import testing

DAY = date(2026, 11, 13)


def test_each_loader_answers_its_sample_rows_with_numbers_as_floats_and_ids_as_text() -> None:
    with testing.fakes():
        prices, measures, curve = fi_pricing.prices(DAY), fi_risk.measures(DAY), fi_curves.treasury_curve(DAY)
    assert prices["clean_price"].dtype == "float64"
    assert prices["isin"].iloc[0] == "US91282CJL69"
    assert set(measures["book"]) == {"Core Rates", "Credit", "Liquidity"}
    assert curve["tenor"].tolist()[:2] == ["3M", "1Y"]


@pytest.mark.parametrize(("basis", "expected"), [("act/365", 181 / 365), ("act/360", 181 / 360), ("30/360", 0.5)])
def test_a_half_year_by_each_basis(basis: Basis, expected: float) -> None:
    assert year_fraction(date(2026, 1, 1), date(2026, 7, 1), basis) == pytest.approx(expected)
