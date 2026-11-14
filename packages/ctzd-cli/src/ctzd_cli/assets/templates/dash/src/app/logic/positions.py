"""The holdings' rules, as pure functions of frames: frames in, a frame out. Nothing here reads anything or imports
Dash, so a job can run them as they stand, and tests can check them on a few rows."""

import pandas as pd

BASIS_POINT = 0.0001
DAYS_A_YEAR = 365.0


def positions(holdings: pd.DataFrame, book: str) -> pd.DataFrame:
    """Each holding of `book`, or of every book where it is empty, with its market value (par times its clean price per
    hundred), its weight of the total, its DV01 (the money it moves by for a basis point of yield) and its day's P&L
    (the price's move plus a day's coupon)."""
    kept = holdings[holdings["book"] == book] if book else holdings
    market_value = kept["par"] * kept["price"] / 100
    prior = kept["par"] * kept["prior_price"] / 100
    pnl = market_value - prior + kept["par"] * kept["coupon"] / DAYS_A_YEAR
    total = market_value.sum()
    return kept.assign(market_value=market_value, weight=market_value / total if total else 0.0, dv01=market_value * kept["duration"] * BASIS_POINT, pnl=pnl).reset_index(drop=True)


def by_sector(positions: pd.DataFrame) -> pd.DataFrame:
    """Each sector's market value, DV01 and day's P&L, the largest first."""
    grouped = positions.groupby("sector", as_index=False)[["market_value", "dv01", "pnl"]].sum()
    return grouped.sort_values("market_value", ascending=False, ignore_index=True)


def totals(positions: pd.DataFrame) -> dict[str, float]:
    """The book's market value, DV01 and day's P&L, and its yield weighted by market value."""
    market_value = float(positions["market_value"].sum())
    ytw = float((positions["ytw"] * positions["market_value"]).sum() / market_value) if market_value else float("nan")
    return {"market_value": market_value, "dv01": float(positions["dv01"].sum()), "pnl": float(positions["pnl"].sum()), "ytw": ytw}
