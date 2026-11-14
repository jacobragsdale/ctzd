"""The sample source's loaders. They never run: a sample source always serves its rows from `fixtures/`, so their
bodies only say what a real loader of the same shape would return."""

from datetime import date

import pandas as pd

from ctzd_kit.loaders import loader


@loader
def holdings(as_of: date) -> pd.DataFrame:
    """Every bond each book held at the close of `as_of`, with its price and yield then and at the close before, its
    duration, its option-adjusted spread, and the Treasury curve's move at its tenor. Rates are fractions."""
    raise NotImplementedError(as_of)


@loader
def trades(start: date, end: date) -> pd.DataFrame:
    """Every trade booked from `start` up to but not including `end`: its account, security, side, quantity, price and
    when it was booked."""
    raise NotImplementedError((start, end))


@loader
def accounts() -> pd.DataFrame:
    """Every account, with its name and whether it is closed."""
    raise NotImplementedError
