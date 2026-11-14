"""Sample rows: a CSV file read as the real read would bring it, so a page or a job sees on the fakes the types it
sees for real. A column named `id` or `..._id` stays text, as ids are selected, and so does a number written with a
leading zero, as an account (00123456) or a SEDOL (0263494) is; text that is an ISO date in every row becomes a date;
`true` and `false` become booleans; any other number is a float64; an empty cell is missing."""

import io
import re
from pathlib import Path

import pandas as pd

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")
_NUMBER = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")
_LEADING_ZERO = re.compile(r"[-+]?0\d")
_BOOLEANS = frozenset({"True", "False", "true", "false"})


def is_id(column: str) -> bool:
    return column == "id" or column.endswith("_id")


def _typed(column: str, text: pd.Series) -> pd.Series:
    present = text.dropna()
    if is_id(column) or present.empty:
        return text
    if present.str.fullmatch(_ISO_DATE).all():
        return pd.to_datetime(text, format="ISO8601")
    if present.isin(_BOOLEANS).all():
        return text.map(lambda value: None if pd.isna(value) else value.lower() == "true").astype("boolean")
    if present.str.fullmatch(_NUMBER).all() and not present.str.match(_LEADING_ZERO).any():
        return pd.to_numeric(text).astype("float64")
    return text


def read_text(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text), dtype="str", keep_default_na=False, na_values=[""])
    return pd.DataFrame({column: _typed(str(column), raw[column]) for column in raw.columns}, index=raw.index)


def read(path: Path) -> pd.DataFrame:
    return read_text(path.read_text(encoding="utf-8"))
