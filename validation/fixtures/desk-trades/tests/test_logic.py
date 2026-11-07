import pandas as pd
import pytest

from app.logic.summary import notional_by_desk


def trades(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame({"desk": [desk for desk, _ in rows], "notional": [notional for _, notional in rows]})


def test_notional_adds_up_per_desk_largest_first() -> None:
    summary = notional_by_desk(trades([("Rates", 1_000_000.0), ("Credit", 5_000_000.0), ("Rates", 2_000_000.0)]))
    assert summary["desk"].tolist() == ["Credit", "Rates"]
    assert summary["notional"].tolist() == pytest.approx([5_000_000.0, 3_000_000.0], abs=1e-9)


def test_no_trades_give_no_desks() -> None:
    assert notional_by_desk(trades([])).empty
