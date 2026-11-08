import ctzd.api
import ctzd.testing
import pandas as pd
from starlette.testclient import TestClient


def trades() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_id": ["1", "2"],
            "trade_date": pd.to_datetime(["2026-11-06", "2026-11-05"]),
            "isin": ["XS1", "XS2"],
            "side": ["BUY", "SELL"],
            "notional": [1_000_000.0, 2_000_000.0],
            "desk": ["Credit", "Rates"],
        }
    )


def test_the_pages_get_the_desks_and_the_trades() -> None:
    with ctzd.testing.fake_sources(fi_trading={"sql/trades.sql": trades()}), TestClient(ctzd.api.asgi("app.api.datasets")) as client:
        desks = client.get("/api/datasets/desk-notional")
        listed = client.get("/api/datasets/trades")
    assert desks.json() == [{"desk": "Rates", "notional": 2_000_000.0}, {"desk": "Credit", "notional": 1_000_000.0}]
    assert [row["trade_id"] for row in listed.json()] == ["1", "2"]
