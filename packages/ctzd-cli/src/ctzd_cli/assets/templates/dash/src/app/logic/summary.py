"""Pure functions: frames in, frames out. No I/O and no UI code here."""

import pandas as pd


def with_share(frame: pd.DataFrame) -> pd.DataFrame:
    """Add `share`, each row's fraction of the total `value`; 0 when the total is 0."""
    total = frame["value"].sum()
    if total == 0:
        return frame.assign(share=0.0)
    return frame.assign(share=frame["value"] / total)
