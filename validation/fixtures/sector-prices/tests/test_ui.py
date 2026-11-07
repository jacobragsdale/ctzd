from pathlib import Path

import pandas as pd
from ctzd.testing import fake_sources
from streamlit.testing.v1 import AppTest

PAGE = Path(__file__).parents[1] / "src" / "app" / "ui" / "main.py"


def latest() -> pd.DataFrame:
    return pd.DataFrame({"isin": ["XS1", "XS2"], "sector": ["Energy", "Utilities"], "clean_price": [100.0, 98.0], "as_of_date": pd.to_datetime(["2026-11-06", "2026-11-06"])})


def test_page_shows_the_sector_prices() -> None:
    with fake_sources(fi_pricing={"sql/latest_prices.sql": latest()}):
        page = AppTest.from_file(str(PAGE)).run(timeout=30)
    assert not page.exception
    assert page.title[0].value == "Sector Prices"
