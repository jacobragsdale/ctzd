"""mas_trades's loaders: the booked trades."""

from datetime import date

import pandas as pd

from ctzd_kit import loader, query


@loader
def trades(start: date, end: date) -> pd.DataFrame:
    """Every trade booked from `start` up to but not including `end`: its portfolio, instrument, side (BUY or SELL),
    quantity, price and when it was booked, in London time."""
    return query("trades.sql", start=start, end=end)
