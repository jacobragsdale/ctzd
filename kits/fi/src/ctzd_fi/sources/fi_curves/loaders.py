"""fi_curves's loaders: the desk's marked curves."""

from datetime import date

import pandas as pd

from ctzd_kit import loader
from ctzd_kit.drivers.rest import rest


@loader
def treasury_curve(as_of: date) -> pd.DataFrame:
    """The Treasury curve marked on `as_of`: each tenor, its years, and its yield as a fraction."""
    return rest().get_frame("/curves/treasury", {"as_of": as_of.isoformat()}, records="points")
