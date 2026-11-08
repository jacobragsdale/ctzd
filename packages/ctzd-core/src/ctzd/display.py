"""`ctzd.display`: numbers as text for emails and Teams messages, written exactly as the page writes them.

It is basis-core's `basis.core.display`, a port of parallax's formatter held to
every case of parallax's own test file: a float is first rounded to 15
significant digits, half away from zero, as Excel does, then to the shown
decimals. So 2.675 shows as 2.68, 0.1 + 0.2 as 0.30, and a missing value (None
or NaN) as empty text. Rates and spreads are fractions, as everywhere else.

    ctzd.display.number(1234.5)               # "1,234.50"
    ctzd.display.number(42.0, decimals=0)     # "42"
    ctzd.display.percent(0.0525)              # "5.25%"
    ctzd.display.bps(0.0525, decimals=0)      # "525 bp"
    ctzd.display.money(-1234.5, "USD")        # "-$1,234.50"
    ctzd.display.price(101.25, decimals=3)    # "101.250"
    ctzd.display.price32(99.515625)           # "99-17", or "99-16+" with step="half"

`text(value, format)` takes any display format a dataset's column declares,
such as `{"kind": "money", "currency": "EUR"}`, or a `Format` for the
formatter's other choices: `text(1_250_000.0, Format(abbreviate="M"))` is
`"1.3M"`. Never round a number yourself: keep the float, and turn it into text
only where it leaves for a person.
"""

from typing import Literal

from basis.core.display import Format, text

type Number = float | int | None
type Step = Literal["32nd", "half", "quarter", "eighth"]

__all__ = ["Format", "bps", "money", "number", "percent", "price", "price32", "text"]


def _shown(value: Number, kind: str, decimals: int | None, **options: str) -> str:
    display: dict[str, object] = {"kind": kind, **options}
    if decimals is not None:
        display["decimals"] = decimals
    return text(value, display)


def number(value: Number, *, decimals: int | None = None) -> str:
    """A number with thousands separators, to `decimals` places (2 by default)."""
    return _shown(value, "number", decimals)


def percent(value: Number, *, decimals: int | None = None) -> str:
    """A fraction as a percentage: 0.0525 is "5.25%"."""
    return _shown(value, "pct", decimals)


def bps(value: Number, *, decimals: int | None = None) -> str:
    """A fraction in basis points: 0.0525 is "525.00 bp", or "525 bp" with `decimals=0`."""
    return _shown(value, "bps", decimals)


def money(value: Number, currency: str, *, decimals: int | None = None) -> str:
    """An amount in `currency`, an ISO 4217 code, to its minor units unless `decimals` says otherwise."""
    return _shown(value, "money", decimals, currency=currency)


def price(value: Number, *, decimals: int | None = None) -> str:
    """A price per 100 of par."""
    return _shown(value, "price", decimals)


def price32(value: Number, *, step: Step = "32nd") -> str:
    """A Treasury price in 32nds, rounded to `step`: 99.515625 is "99-17" in 32nds and "99-16+" in halves."""
    return _shown(value, "price32", None, step=step)
