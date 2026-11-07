"""Pure functions: frames in, frames out."""

import pandas as pd


def notional_by_desk(trades: pd.DataFrame) -> pd.DataFrame:
    """Total traded notional per desk, largest first."""
    summary = trades.groupby("desk", as_index=False).agg(notional=("notional", "sum"))
    return summary.sort_values("notional", ascending=False, ignore_index=True)
