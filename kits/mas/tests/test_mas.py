"""MAS's loaders answer their sample rows on the fakes, and drift is measured against the targets."""

from datetime import date

import pandas as pd
import pytest

from ctzd_kit import testing
from ctzd_mas import mas_reports, mas_trades
from ctzd_mas.helpers.rebalance import drift


def test_each_loader_answers_its_sample_rows() -> None:
    with testing.fakes():
        trades, allocations = mas_trades.trades(date(2026, 11, 13), date(2026, 11, 14)), mas_reports.allocations(date(2026, 11, 13))
    assert trades["quantity"].dtype == "float64"
    assert trades["booked_at"].dtype.kind == "M"
    assert allocations.groupby("portfolio_id")["weight"].sum().round(9).eq(1.0).all()


def test_drift_is_the_weight_less_its_target_and_the_largest_is_by_portfolio() -> None:
    rows = pd.DataFrame({"portfolio_id": ["P", "P", "Q"], "asset_class": ["Equity", "Cash", "Equity"], "weight": [0.6, 0.4, 1.0], "target_weight": [0.5, 0.5, 1.0]})
    found = drift(rows)
    assert found["drift"].tolist() == pytest.approx([0.1, -0.1, 0.0])
    assert found["largest_drift"].tolist() == pytest.approx([0.1, 0.1, 0.0])
