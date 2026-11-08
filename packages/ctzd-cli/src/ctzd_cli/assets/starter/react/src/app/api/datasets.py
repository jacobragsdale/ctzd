"""Datasets: what the page reads. Each is a function of the page's parameters that returns a frame.

The page gets each dataset's rows from `/api/datasets/NAME` through the client that
`ctzd dev` and `ctzd check` generate from these declarations. A row type names each
column the page shows and how it shows it; every number is a float.
"""

import ctzd
import pandas as pd

from app.data.loaders import load_demo
from app.logic.summary import with_share


class CategoryShare(ctzd.Row):
    """One category's value and its share of the total."""

    category: str
    value: ctzd.Number
    share: ctzd.Pct


@ctzd.dataset(CategoryShare)
def category_shares() -> pd.DataFrame:
    """Each category's value and its share of the total."""
    return with_share(load_demo())
