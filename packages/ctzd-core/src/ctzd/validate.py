"""`ctzd.require_columns`: a light check that a frame has the columns and dtype families code relies on."""

from collections.abc import Callable, Mapping
from typing import Literal

import pandas as pd
from pandas.api import types as ptypes

from ctzd._errors import DataError

Family = Literal["any", "bool", "datetime", "float", "int", "number", "str"]

_CHECKS: dict[Family, Callable[[pd.Series], bool]] = {
    "any": lambda _dtype: True,
    "bool": ptypes.is_bool_dtype,
    "datetime": ptypes.is_datetime64_any_dtype,
    "float": ptypes.is_float_dtype,
    "int": ptypes.is_integer_dtype,
    "number": ptypes.is_numeric_dtype,
    "str": ptypes.is_string_dtype,
}


def require_columns(frame: pd.DataFrame, columns: Mapping[str, Family], *, what: str = "frame") -> pd.DataFrame:
    """Return `frame` unchanged if every named column exists with the right dtype family; raise otherwise."""
    problems: list[str] = []
    for column, family in columns.items():
        if column not in frame.columns:
            problems.append(f"missing column {column!r}")
        elif not _CHECKS[family](frame[column]):
            problems.append(f"column {column!r} is {frame[column].dtype}, expected {family}")
    if problems:
        msg = f"{what}: " + "; ".join(problems)
        raise DataError(msg)
    return frame
