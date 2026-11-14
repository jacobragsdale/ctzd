"""{{source}}'s loaders: the reads an app may make of it, each with sample rows in fixtures/. A package source calls
the department's own package, which makes its own connection; list the package in source.toml's `io`, add it to the
kit's dependencies, and name the variables it reads in `[secret.env]`."""

from datetime import date

import pandas as pd
from ctzd_kit import SourceError, loader


@loader
def example(as_of: date) -> pd.DataFrame:
    """Rename this loader and say what it returns, in the department's words: the skill and `ctzd sources` show it."""
    msg = f"Call the department's package here, as `return riskpackage.measures({as_of!r})`."
    raise SourceError(msg)
