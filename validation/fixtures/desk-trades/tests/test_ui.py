from pathlib import Path

import pandas as pd
from ctzd.testing import fake_sources
from streamlit.testing.v1 import AppTest

PAGE = Path(__file__).parents[1] / "src" / "app" / "ui" / "main.py"


def trades() -> pd.DataFrame:
    return pd.DataFrame({
        "trade_id": [1, 2],
        "trade_date": pd.to_datetime(["2026-11-05", "2026-11-06"]),
        "isin": ["XS1", "XS2"],
        "side": ["BUY", "SELL"],
        "notional": [1_000_000.0, 2_000_000.0],
        "desk": ["Credit", "Rates"],
    })


def test_page_shows_the_trades() -> None:
    with fake_sources(fi_trading={"sql/trades.sql": trades()}):
        page = AppTest.from_file(str(PAGE)).run(timeout=30)
    assert not page.exception
    assert page.title[0].value == "Desk Trades"
