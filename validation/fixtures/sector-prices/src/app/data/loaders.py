"""Loaders: the only code in this app that reads data."""

import ctzd
import pandas as pd


@ctzd.cached(ttl="15m")
def load_latest_prices() -> pd.DataFrame:
    frame = ctzd.query("fi_pricing", "sql/latest_prices.sql")
    return ctzd.require_columns(frame, {"isin": "str", "sector": "str", "clean_price": "float", "as_of_date": "datetime"})
