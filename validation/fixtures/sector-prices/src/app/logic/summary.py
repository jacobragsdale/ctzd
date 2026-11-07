"""Pure functions: frames in, frames out."""

import pandas as pd


def price_by_sector(prices: pd.DataFrame) -> pd.DataFrame:
    """One row per sector with its average clean price (per 100 par), highest first."""
    summary = prices.groupby("sector", as_index=False)["clean_price"].mean()
    return summary.sort_values("clean_price", ascending=False, ignore_index=True)
