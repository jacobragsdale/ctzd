"""Loaders: the only code in this app that reads data."""

import ctzd
import pandas as pd


@ctzd.cached(ttl="15m")
def load_trades() -> pd.DataFrame:
    frame = ctzd.query("fi_trading", "sql/trades.sql")
    return ctzd.require_columns(frame, {"trade_id": "str", "trade_date": "datetime", "isin": "str", "side": "str", "notional": "float", "desk": "str"}, what="trades")
