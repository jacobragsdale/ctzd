"""Datasets: what the page reads."""

import ctzd
import pandas as pd

from app.data.loaders import load_latest_prices
from app.logic.summary import price_by_sector


class SectorPrice(ctzd.Row):
    """One sector's average clean price on the latest day."""

    sector: str
    clean_price: ctzd.Price


@ctzd.dataset(SectorPrice)
def sector_prices() -> pd.DataFrame:
    """Each sector's average clean price (per 100 par) on the latest day, highest first."""
    return price_by_sector(load_latest_prices())
