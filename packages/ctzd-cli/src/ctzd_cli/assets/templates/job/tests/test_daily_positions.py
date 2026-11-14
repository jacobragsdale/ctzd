"""The job's rules on a few rows written here, and the job itself on the sample rows."""

import pandas as pd
import pytest

from app.jobs import daily_positions
from app.logic import positions as rules

ACCOUNTS = pd.DataFrame({"account_id": ["A1", "A2"], "name": ["Alpha", "Closed"], "closed": [False, True]})
TRADE = {
    "trade_id": "T1",
    "account_id": "A1",
    "security_id": "S1",
    "trade_date": pd.Timestamp("2026-11-12"),
    "side": "BUY",
    "quantity": 100.0,
    "price": 101.0,
    "booked_at": pd.Timestamp("2026-11-12 09:30"),
}


def trades(*changes: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame([TRADE | change for change in changes])


def test_a_sale_nets_against_a_purchase_and_notional_is_gross() -> None:
    found = rules.daily_positions(trades({}, {"trade_id": "T2", "side": "SELL", "quantity": 40.0}), ACCOUNTS)
    assert found[["net_quantity", "gross_notional", "trades"]].iloc[0].tolist() == [60.0, pytest.approx(14_140.0), 2.0]


def test_closed_accounts_are_left_out_unless_asked_for() -> None:
    closed = trades({"account_id": "A2"})
    assert rules.daily_positions(closed, ACCOUNTS).empty
    assert len(rules.daily_positions(closed, ACCOUNTS, include_closed=True)) == 1


def test_unknown_accounts_and_sides_are_reasons_not_to_write() -> None:
    assert rules.problems(trades({"account_id": "A9"}, {"side": "HOLD"}), ACCOUNTS) == [
        "1 trades are in accounts the accounts table does not list",
        "trades have sides other than BUY and SELL: ['HOLD']",
    ]


def test_the_job_runs_on_the_sample_rows() -> None:
    assert daily_positions.main() == 0
