"""{{source}}'s loaders: the reads an app may make of it, each with sample rows in fixtures/."""

from datetime import date

import pandas as pd
from ctzd_kit import loader, query


@loader
def example(as_of: date) -> pd.DataFrame:
    """Rename this loader and say what it returns, in the department's words: the skill and `ctzd sources` show it."""
    return query("example.sql", as_of=as_of)
