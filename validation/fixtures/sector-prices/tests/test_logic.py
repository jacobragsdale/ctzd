import pandas as pd
import pytest

from app.logic.summary import price_by_sector


def prices(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame({"sector": [sector for sector, _ in rows], "clean_price": [price for _, price in rows]})


def test_each_sector_gets_its_average_price_highest_first() -> None:
    summary = price_by_sector(prices([("Energy", 100.0), ("Energy", 90.0), ("Utilities", 99.0)]))
    assert summary["sector"].tolist() == ["Utilities", "Energy"]
    assert summary["clean_price"].tolist() == pytest.approx([99.0, 95.0], abs=1e-9)


def test_no_prices_give_no_sectors() -> None:
    assert price_by_sector(prices([])).empty
