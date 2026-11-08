"""Datasets: what the pages read."""

import datetime as dt
from typing import Annotated

import ctzd
import pandas as pd

from app.data.loaders import load_trades
from app.logic.summary import notional_by_desk


class DeskNotional(ctzd.Row):
    """One desk's total traded notional."""

    desk: str
    notional: Annotated[float | None, ctzd.money("USD", decimals=0)]


class Trade(ctzd.Row):
    """One trade."""

    trade_id: str
    trade_date: dt.date
    isin: str
    side: str
    notional: Annotated[float | None, ctzd.money("USD", decimals=0)]
    desk: str


@ctzd.dataset(DeskNotional)
def desk_notional() -> pd.DataFrame:
    """Total traded notional per desk, largest first."""
    return notional_by_desk(load_trades())


@ctzd.dataset(Trade)
def trades() -> pd.DataFrame:
    """Every trade, newest first."""
    return load_trades()
