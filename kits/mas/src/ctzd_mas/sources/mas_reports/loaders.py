"""mas_reports's loaders: the nightly allocation reports."""

from datetime import date

import pandas as pd

from ctzd_kit import loader
from ctzd_kit.drivers import files


@loader
def allocations(as_of: date) -> pd.DataFrame:
    """Each portfolio's weight in each asset class at the close of `as_of`, and its target weight, both as fractions
    of the portfolio."""
    return files.read(f"allocations/{as_of.isoformat()}.xlsx")
