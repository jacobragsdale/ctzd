"""fi_risk's loaders: the desk's risk library, called as it stands."""

from datetime import date

import pandas as pd

import fi_risklib
from ctzd_kit import loader


@loader
def measures(as_of: date) -> pd.DataFrame:
    """DV01 and spread duration of every position on `as_of`, by book and ISIN. DV01 is in US dollars per basis point."""
    return fi_risklib.measures(as_of)
