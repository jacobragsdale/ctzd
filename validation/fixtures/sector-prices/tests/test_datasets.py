import ctzd.api
import ctzd.testing
import pandas as pd
from starlette.testclient import TestClient


def latest() -> pd.DataFrame:
    return pd.DataFrame(
        {"isin": ["XS1", "XS2", "XS3"], "sector": ["Energy", "Energy", "Utilities"], "clean_price": [100.0, 90.0, 99.0], "as_of_date": pd.to_datetime(["2026-11-06", "2026-11-06", "2026-11-06"])}
    )


def test_the_page_gets_each_sector_s_price() -> None:
    with ctzd.testing.fake_sources(fi_pricing={"sql/latest_prices.sql": latest()}), TestClient(ctzd.api.asgi("app.api.datasets")) as client:
        answer = client.get("/api/datasets/sector-prices")
    assert answer.status_code == 200
    assert answer.json() == [{"sector": "Utilities", "clean_price": 99.0}, {"sector": "Energy", "clean_price": 95.0}]
