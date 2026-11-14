"""The app's only reads. A page's callback awaits one of these through `ui.load`; a job calls one directly.

Two ways to read, both through the department's kit: call one of its loaders (`holdings`), or run the app's own SQL
file, in `sql/`, against one of its database sources (`curve`). The `sbu-{{sbu}}` skill and `ctzd sources` list what
the kit has. Both reads here use `sample`, made-up rows every kit has: replace them with the person's real source.
"""

from datetime import date

import pandas as pd
from {{kit}} import query, sample


def holdings(as_of: date) -> pd.DataFrame:
    """Every bond each book held at the close of `as_of`, priced then and at the close before."""
    return sample.holdings(as_of)


def curve(as_of: date) -> pd.DataFrame:
    """The Treasury curve at the close of `as_of`: each tenor, its years and its yield."""
    return query("sample", "curve.sql", as_of=as_of)
