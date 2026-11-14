"""Day counts: how much of a year lies between two dates, by the conventions FI's bonds use."""

from datetime import date
from typing import Final, Literal

type Basis = Literal["act/365", "act/360", "30/360"]
MONTH: Final = 30
"""A month's days in the 30/360 convention."""


def year_fraction(start: date, end: date, basis: Basis = "act/365") -> float:
    """The fraction of a year from `start` to `end`, negative when `end` is earlier."""
    if basis == "30/360":
        first = min(start.day, MONTH)
        last = min(end.day, MONTH) if first == MONTH else end.day
        return ((end.year - start.year) * 360 + (end.month - start.month) * MONTH + (last - first)) / 360
    return (end - start).days / (360 if basis == "act/360" else 365)
