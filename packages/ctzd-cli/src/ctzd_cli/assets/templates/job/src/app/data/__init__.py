"""The app's only reads and writes, through the department's kit: its loaders for the inputs, and its store for the
tables a job writes. The `sbu-{{sbu}}` skill and `ctzd sources` list what the kit has. The reads here use `sample`,
made-up rows every kit has: replace them with the person's real source.
"""

from datetime import date

import pandas as pd
from {{kit}} import sample, store


def trades(start: date, end: date) -> pd.DataFrame:
    """Every trade booked from `start` up to but not including `end`."""
    return sample.trades(start, end)


def accounts() -> pd.DataFrame:
    """Every account, with its name and whether it is closed."""
    return sample.accounts()


def write(frame: pd.DataFrame, table: str) -> str:
    """Publish `frame` as the new version of the app's table `table`, and return the version."""
    return store.write_table(frame, table)


def read(table: str) -> pd.DataFrame:
    """The latest version of the app's table `table`, as a job wrote it."""
    return store.read_table(table)
