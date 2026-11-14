"""fi_pricing's loaders: the published bond prices."""

from datetime import date

import pandas as pd

from ctzd_kit import loader, query


@loader
def prices(as_of: date) -> pd.DataFrame:
    """Every bond's published price on `as_of`: its ISIN, issuer and sector, its clean price per 100 of par, its yield
    to worst and its option-adjusted spread, both as fractions."""
    return query("prices.sql", as_of=as_of)
