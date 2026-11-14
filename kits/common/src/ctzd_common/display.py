"""Numbers as text, written exactly as a page's grid writes them: the one rounding rule, in one place.

A float is first rounded to 15 significant digits from its exact value, half away from zero, as Excel does, then once
to the decimals shown. So 2.675 shows as 2.68 (where Python's `round` gives 2.67), 0.1 + 0.2 as 0.30, a missing value
(None or NaN) as empty text, and a value that rounds to zero with no minus sign. Rates and spreads are fractions:
0.0525 is 5.25% and 525 bp.

    display.number(1234.5)                 # "1,234.50"
    display.number(42.0, decimals=0)       # "42"
    display.percent(0.0525)                # "5.25%"
    display.bps(0.0525, decimals=0)        # "525 bp"
    display.money(-1234.5, "USD")          # "-$1,234.50"
    display.price(101.25, decimals=3)      # "101.250"
    display.price32(99.515625)             # "99-17", or "99-16+" with step="half"

A page names a `Format` for each column and axis (the app's `ui.py` gives them), and an email or a Slack message
writes a table with the same formats, so a number reads the same everywhere. Never round a number yourself: keep the
float, and turn it into text only where it leaves for a person.
"""

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Final, Literal

type Number = float | int | None
type Kind = Literal["number", "pct", "bps", "money", "price", "price32"]
type Step = Literal["32nd", "half", "quarter", "eighth"]

SIGNIFICANT: Final = 15
DEFAULT_DECIMALS: Final = 2
BP: Final = "\N{NO-BREAK SPACE}bp"
CURRENCIES: Final[dict[str, tuple[str, int]]] = {
    "USD": ("$", 2),
    "EUR": ("€", 2),
    "GBP": ("£", 2),
    "JPY": ("¥", 0),
    "CHF": ("CHF\N{NO-BREAK SPACE}", 2),
    "CAD": ("CA$", 2),
    "AUD": ("A$", 2),
    "HKD": ("HK$", 2),
    "SGD": ("S$", 2),
    "CNY": ("CN¥", 2),
    "INR": ("₹", 2),
    "SEK": ("SEK\N{NO-BREAK SPACE}", 2),
    "NOK": ("NOK\N{NO-BREAK SPACE}", 2),
    "DKK": ("DKK\N{NO-BREAK SPACE}", 2),
}
"""Each currency a format may name: its symbol, and its minor units, the decimals it shows by default."""
STEPS: Final[dict[Step, int]] = {"32nd": 1, "half": 2, "quarter": 4, "eighth": 8}
_EIGHTHS_PER_POINT: Final = 256
_PRICE_DECIMALS: Final = 8


class FormatError(ValueError):
    """A format that cannot be: the message says what to give instead."""


@dataclass(frozen=True)
class Format:
    """How a column's or an axis's numbers show: `kind`, its decimals, and a money format's currency or a price in 32nds'
    finest step."""

    kind: Kind = "number"
    decimals: int | None = None
    currency: str | None = None
    step: Step | None = None

    def __post_init__(self) -> None:
        if self.decimals is not None and not 0 <= self.decimals <= SIGNIFICANT:
            msg = f"decimals must be 0 to {SIGNIFICANT}, not {self.decimals}"
            raise FormatError(msg)
        if self.kind == "money" and self.currency not in CURRENCIES:
            msg = f"currency {self.currency!r} is not one ctzd knows: give one of {', '.join(CURRENCIES)}"
            raise FormatError(msg)
        if self.kind != "money" and self.currency is not None:
            msg = "only a money format takes a currency"
            raise FormatError(msg)
        if self.step is not None and self.kind != "price32":
            msg = "only a price in 32nds takes a step"
            raise FormatError(msg)

    @property
    def shown_decimals(self) -> int:
        if self.decimals is not None:
            return self.decimals
        if self.kind == "money" and self.currency is not None:
            return CURRENCIES[self.currency][1]
        return DEFAULT_DECIMALS

    def spec(self) -> dict[str, object]:
        """What the browser's formatter takes for this format, every default resolved: `ui.py` writes it into a grid
        column's valueFormatter, so the grid rounds as this module does."""
        if self.kind == "price32":
            return {"thirtySeconds": STEPS[self.step or "32nd"]}
        found: dict[str, object] = {"decimals": self.shown_decimals}
        if self.kind == "pct":
            found["percent"] = True
        if self.kind == "bps":
            found["bps"] = True
        if self.kind == "money" and self.currency is not None:
            found["symbol"] = CURRENCIES[self.currency][0]
        return found


def _missing(value: Number) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _fifteen(value: float) -> Decimal:
    """The magnitude of `value`, rounded to 15 significant digits from its exact binary value, half away from zero."""
    exact = Decimal(abs(value))
    if exact == 0:
        return exact
    with localcontext() as context:
        context.prec = 400
        return exact.quantize(Decimal(1).scaleb(exact.adjusted() - SIGNIFICANT + 1), rounding=ROUND_HALF_UP)


def _rounded(magnitude: Decimal, decimals: int) -> Decimal:
    with localcontext() as context:
        context.prec = 400
        return magnitude.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def text(value: Number, given: Format | None = None) -> str:
    """`value` as people read it, in `given`, or as a plain number with two decimals."""
    shown = given or Format()
    if value is None or _missing(value):
        return ""
    number = float(value)
    negative = math.copysign(1.0, number) < 0
    if math.isinf(number):
        return ("-" if negative else "") + "∞"
    if shown.kind == "price32":
        return _price32(number, shown.step or "32nd", negative=negative)
    magnitude = _fifteen(number)
    if shown.kind == "pct":
        magnitude *= 100
    elif shown.kind == "bps":
        magnitude *= 10_000
    rounded = _rounded(magnitude, shown.shown_decimals)
    body = format(rounded, f",.{shown.shown_decimals}f")
    sign = "-" if negative and rounded != 0 else ""
    if shown.kind == "money" and shown.currency is not None:
        return sign + CURRENCIES[shown.currency][0] + body
    return sign + body + ("%" if shown.kind == "pct" else BP if shown.kind == "bps" else "")


def _price32(value: float, step: Step, *, negative: bool) -> str:
    with localcontext() as context:
        context.prec = 400
        short = Decimal(repr(abs(value))).quantize(Decimal(1).scaleb(-_PRICE_DECIMALS), rounding=ROUND_HALF_UP)
        per_point = 32 * STEPS[step]
        units = int((short * per_point).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    eighths = units * (_EIGHTHS_PER_POINT // per_point)
    whole, rest = divmod(eighths, _EIGHTHS_PER_POINT)
    thirty_seconds, eighth = divmod(rest, 8)
    mark = "" if eighth == 0 else "+" if eighth == 4 else str(eighth)  # noqa: PLR2004 - four eighths is a half
    body = f"{whole}-{thirty_seconds:02d}{mark}"
    return f"-{body}" if negative and eighths else body


def number(value: Number, *, decimals: int | None = None) -> str:
    """A number with thousands separators, to `decimals` places (2 by default)."""
    return text(value, Format("number", decimals))


def percent(value: Number, *, decimals: int | None = None) -> str:
    """A fraction as a percentage: 0.0525 is "5.25%"."""
    return text(value, Format("pct", decimals))


def bps(value: Number, *, decimals: int | None = None) -> str:
    """A fraction in basis points: 0.0525 is "525.00 bp", or "525 bp" with `decimals=0`."""
    return text(value, Format("bps", decimals))


def money(value: Number, currency: str, *, decimals: int | None = None) -> str:
    """An amount in `currency`, an ISO 4217 code, to its minor units unless `decimals` says otherwise."""
    return text(value, Format("money", decimals, currency=currency))


def price(value: Number, *, decimals: int | None = None) -> str:
    """A price per 100 of par."""
    return text(value, Format("price", decimals))


def price32(value: Number, *, step: Step = "32nd") -> str:
    """A Treasury price in 32nds, rounded to `step`: 99.515625 is "99-17" in 32nds and "99-16+" in halves."""
    return text(value, Format("price32", step=step))
