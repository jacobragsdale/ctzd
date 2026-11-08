"""Text for numbers that a job writes for people, in an email body or a Teams message.

The web UI's grid and number components show numbers through parallax's formatter, which follows Excel's display
rules. This module ports that formatter (`parallax-engine/src/format.rs`, by way of its TypeScript port in basis-ui's
`format/format.ts`) and the text the components put around it (`components/num-text.ts`), so a float reads the same
in a job's message as on the page:

- A float is first rounded to 15 significant digits, half away from zero, as Excel does: 2.675, whose double is
  2.67499999999999982..., becomes 2.67500000000000, so it shows as 2.68, and 0.1 + 0.2 shows as 0.3.
- Percent, basis-point, and K/M/B scaling then move the decimal point exactly, and the result is rounded once to the
  shown decimals, half away from zero.
- A value that rounds to zero never shows a minus sign: -0.0001 at 2 decimals is `0.00`.

The functions return text and never change a value. Keep the float for any further arithmetic and format it only
where the text leaves for a person.

Each function matches one component, and the keywords match the TypeScript options one to one, with the same values:

| Python | basis-ui | Keywords |
|---|---|---|
| `number(value)` | `Num`, `numText` | `decimals`, `thousands`, `abbreviate` (`"K"`, `"M"`, `"B"`), `negative` (`"minus"`, `"parentheses"`) |
| `percent(value)` | `Pct`, `pctText` | `decimals`, `thousands`, `negative` |
| `bps(value)` | `Bps`, `bpsText` | as `number` |
| `money(value, currency)` | `Money`, `moneyText` | `decimals`, `abbreviate`, `negative` |
| `price32(value)` | `Price32`, `price32Text` | `precision`, `rounding` |

A value is a float or an int, or their numpy scalars, as a DataFrame holds them:

- A float takes the formatter's float path, and shows 2 decimals unless told otherwise (1 when abbreviated). Every
  number read from a source or the store is a float, a count included: `number(count, decimals=0)` shows it whole.
- An int, such as `len(frame)`, takes the integer path, as a TypeScript `bigint` does: exact at any size, and 0
  decimals unless told otherwise.
- `None` and NaN show `NULL_TEXT`, the formatter's text for a missing value. pandas marks a missing float with NaN,
  and Arrow, so a stored table and the grid that shows it, reads a pandas NaN as null.
- The infinities show as the formatter writes them: `∞`, `-∞`, or `(∞)`.
- A bool is not a number and raises `CtzdError`, as does any other type, and so does an option the function does not
  offer.

Where this differs from the TypeScript:

- NaN shows `NULL_TEXT` rather than `UNDEFINED_TEXT`, for the reason above.
- Values are only floats and ints: there is no decimal-text or `Decimal` input, since every number here is a float64.
- `percent` and `bps` take a fraction only (0.0525 is 5.25% and 525 bp); the TypeScript's `unit` option is left out.
  For a rate held in percent or basis points, divide it first: `percent(rate / 100)` shows what `unit: "percent"` does.
- `percent` takes no `abbreviate`, which the formatter refuses alongside a percentage, and `money` no `thousands`:
  an amount always groups its thousands.
- The functions return the text only, not the gain or loss direction the components also report.
- Options are checked even when the value is missing, so a bad option fails on the first run, not on the first day
  the value is present.
- Currency symbols and minor units come from a table of what Node 26's `Intl.NumberFormat` (ICU 78, CLDR 48) writes
  for US English, rather than from `Intl` at run time.
"""

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast

import numpy as np

from ctzd._errors import CtzdError

type Value = float | int | np.floating | np.integer | None
"""A number to show: a float or an int, a numpy scalar of either, or `None` for a missing value."""

type Abbreviation = Literal["K", "M", "B"]
"""A fixed scaling by a thousand, million, or billion. The suffix is always shown."""

type Negative = Literal["minus", "parentheses"]
"""How a negative number is written: `-1,234.50` or `(1,234.50)`."""

type Precision = Literal["thirty-seconds", "halves", "quarters", "eighths"]
"""The finest step a Treasury price shows: a 32nd, or a half, quarter, or eighth of one."""

type Rounding = Literal["half-away-from-zero", "half-even", "toward-zero", "away-from-zero", "floor", "ceiling"]
"""How a price between two steps rounds. `half-away-from-zero` is Excel's `ROUND`; `half-even` is banker's rounding;
`toward-zero` truncates; `away-from-zero` moves any remainder away from zero; `floor` and `ceiling` round toward
negative and positive infinity."""

NULL_TEXT: Final = ""
"""The text for a missing value: `None` or NaN."""

UNDEFINED_TEXT: Final = "\N{EM DASH}"
"""The text for a result that has no value, such as the 32nds of an infinite price."""

MAX_DECIMALS: Final = 30
"""The most decimals a number may show, Excel's limit."""

_INFINITY: Final = "\N{INFINITY}"
_SIGNIFICANT_DIGITS: Final = 15
_PERCENT_POWER: Final = 2
_DEFAULT_DECIMALS: Final = {"float": 2, "integer": 0, "percent": 2, "abbreviated": 1}
_ABBREVIATION_POWERS: Final[dict[Abbreviation, int]] = {"K": 3, "M": 6, "B": 9}
_SIGNS: Final[dict[Negative, tuple[str, str]]] = {"minus": ("-", ""), "parentheses": ("(", ")")}
_BPS_PER_WHOLE: Final = 10_000
_BPS_UNIT: Final = "\N{NO-BREAK SPACE}bp"

_CURRENCY_CODE: Final = re.compile(r"[A-Za-z]{3}")
# The en-US symbol and minor units of each currency that is not written as its code with 2 decimals, as Node 26's
# Intl.NumberFormat (ICU 78.3, CLDR 48) writes them. Any other well-formed code is written as itself, with 2 decimals.
_CURRENCIES: Final[dict[str, tuple[str, int]]] = {
    "AFN": ("AFN", 0),
    "ALL": ("ALL", 0),
    "AUD": ("A$", 2),
    "BHD": ("BHD", 3),
    "BIF": ("BIF", 0),
    "BRL": ("R$", 2),
    "CAD": ("CA$", 2),
    "CLP": ("CLP", 0),
    "CNY": ("CN\N{YEN SIGN}", 2),
    "COP": ("COP", 0),
    "DJF": ("DJF", 0),
    "EUR": ("\N{EURO SIGN}", 2),
    "GBP": ("\N{POUND SIGN}", 2),
    "GNF": ("GNF", 0),
    "HKD": ("HK$", 2),
    "HUF": ("HUF", 0),
    "IDR": ("IDR", 0),
    "ILS": ("\N{NEW SHEQEL SIGN}", 2),
    "INR": ("\N{INDIAN RUPEE SIGN}", 2),
    "IQD": ("IQD", 0),
    "IRR": ("IRR", 0),
    "ISK": ("ISK", 0),
    "JOD": ("JOD", 3),
    "JPY": ("\N{YEN SIGN}", 0),
    "KMF": ("KMF", 0),
    "KPW": ("KPW", 0),
    "KRW": ("\N{WON SIGN}", 0),
    "KWD": ("KWD", 3),
    "LAK": ("LAK", 0),
    "LBP": ("LBP", 0),
    "LYD": ("LYD", 3),
    "MGA": ("MGA", 0),
    "MMK": ("MMK", 0),
    "MXN": ("MX$", 2),
    "NZD": ("NZ$", 2),
    "OMR": ("OMR", 3),
    "PHP": ("\N{PESO SIGN}", 2),
    "PKR": ("PKR", 0),
    "PYG": ("PYG", 0),
    "RWF": ("RWF", 0),
    "SLL": ("SLL", 0),
    "SOS": ("SOS", 0),
    "SYP": ("SYP", 0),
    "TND": ("TND", 3),
    "TWD": ("NT$", 2),
    "UGX": ("UGX", 0),
    "USD": ("$", 2),
    "VND": ("\N{DONG SIGN}", 0),
    "VUV": ("VUV", 0),
    "XAF": ("FCFA", 0),
    "XCD": ("EC$", 2),
    "XCG": ("Cg.", 2),
    "XOF": ("F\N{NARROW NO-BREAK SPACE}CFA", 0),
    "XPF": ("CFPF", 0),
    "YER": ("YER", 0),
}

# A Treasury price counts 256ths of a point: 32 32nds, each of 8 eighths, with four eighths written `+`.
_EIGHTHS_PER_POINT: Final = 256
_EIGHTHS_PER_THIRTY_SECOND: Final = 8
_THIRTY_SECONDS_PER_POINT: Final = 32
_HALF_IN_EIGHTHS: Final = 4
_STEPS_PER_THIRTY_SECOND: Final[dict[Precision, int]] = {"thirty-seconds": 1, "halves": 2, "quarters": 4, "eighths": 8}
# A 256th of a point needs eight decimals, so a float price is read at this scale before it is counted in steps.
_PRICE_SCALE: Final = 8

type _Half = Literal["below", "tie", "above"]
"""Where a remainder sits against half the divisor."""

type _RoundsAway = Callable[[bool, _Half, bool], bool]
"""Whether a rounding mode moves a truncated quotient one step away from zero, given whether the value is negative,
where the remainder sits, and whether the quotient is odd."""

_ROUNDS_AWAY: Final[dict[Rounding, _RoundsAway]] = {
    "half-away-from-zero": lambda _negative, half, _odd: half != "below",
    "half-even": lambda _negative, half, odd: half == "above" or (half == "tie" and odd),
    "toward-zero": lambda _negative, _half, _odd: False,
    "away-from-zero": lambda _negative, _half, _odd: True,
    "floor": lambda negative, _half, _odd: negative,
    "ceiling": lambda negative, _half, _odd: not negative,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class _Settings:
    """A checked number format: the formatter's `Format` without its time fields."""

    decimals: int | None = None
    thousands: bool = True
    percent: bool = False
    abbreviate: Abbreviation | None = None
    negative: Negative = "minus"

    def __post_init__(self) -> None:
        if self.decimals is not None and not 0 <= self.decimals <= MAX_DECIMALS:
            msg = f"decimals={self.decimals} is not a count from 0 to the {MAX_DECIMALS} Excel can show."
            raise CtzdError(msg)
        if self.abbreviate is not None:
            _checked(_ABBREVIATION_POWERS, "abbreviate", self.abbreviate)
        _checked(_SIGNS, "negative", self.negative)

    def text(self, value: float) -> str:
        """The text for a float that is not NaN, or an int."""
        if isinstance(value, int):
            return self._exact(abs(value), 0, is_negative=value < 0, natural=_DEFAULT_DECIMALS["integer"])
        is_negative = math.copysign(1.0, value) < 0
        if math.isinf(value):
            return self._signed(_INFINITY, is_negative=is_negative)
        coefficient, exponent = _fifteen_digits(abs(value))
        return self._exact(coefficient, exponent, is_negative=is_negative, natural=_DEFAULT_DECIMALS["float"])

    def _places(self, natural: int) -> int:
        if self.decimals is not None:
            return self.decimals
        if self.percent:
            return _DEFAULT_DECIMALS["percent"]
        return _DEFAULT_DECIMALS["abbreviated"] if self.abbreviate is not None else natural

    def _exact(self, coefficient: int, exponent: int, *, is_negative: bool, natural: int) -> str:
        """The text for `coefficient * 10**exponent`, signed by `is_negative`."""
        places = self._places(natural)
        power = exponent + (_PERCENT_POWER if self.percent else 0)
        if self.abbreviate is not None:
            power -= _ABBREVIATION_POWERS[self.abbreviate]
        scaled = _shift_half_away(coefficient, power + places)
        whole, fraction = divmod(scaled, _power(10, places))
        body = f"{whole:,}" if self.thousands else str(whole)
        if places > 0:
            body += f".{fraction:0{places}d}"
        suffix = f"{self.abbreviate or ''}{'%' if self.percent else ''}"
        return self._signed(body + suffix, is_negative=is_negative and scaled != 0)

    def _signed(self, body: str, *, is_negative: bool) -> str:
        if not is_negative:
            return body
        before, after = _SIGNS[self.negative]
        return f"{before}{body}{after}"


def _checked[K, V](choices: Mapping[K, V], name: str, choice: K) -> V:
    """What a keyword's choice stands for; a choice the keyword does not offer raises `CtzdError`."""
    if choice not in choices:
        msg = f"{name}={choice!r} is not one of {', '.join(repr(key) for key in choices)}."
        raise CtzdError(msg)
    return choices[choice]


def _power(base: int, exponent: int) -> int:
    """`base**exponent` for an exponent of 0 or more."""
    # typeshed types an int power as Any, since a negative exponent makes a float.
    return cast("int", base**exponent)


def _shift_half_away(coefficient: int, places: int) -> int:
    """`coefficient * 10**places` as an integer, a fraction rounded half away from zero; `coefficient` is not negative."""
    if places >= 0:
        return coefficient * _power(10, places)
    divisor = _power(10, -places)
    quotient, remainder = divmod(coefficient, divisor)
    return quotient + 1 if 2 * remainder >= divisor else quotient


def _fifteen_digits(magnitude: float) -> tuple[int, int]:
    """A finite float that is not negative, rounded to 15 significant digits half away from zero, as a coefficient
    and a decimal exponent."""
    numerator, denominator = magnitude.as_integer_ratio()
    # The denominator is 2**k, and n / 2**k is n * 5**k * 10**-k: the float's exact decimal value.
    k = denominator.bit_length() - 1
    coefficient = numerator * _power(5, k)
    dropped = len(str(coefficient)) - _SIGNIFICANT_DIGITS
    if dropped <= 0:
        return coefficient, -k
    return _shift_half_away(coefficient, -dropped), dropped - k


def _plain(value: Value) -> float | int | None:
    """The value as a Python float or int, or `None` when it is missing."""
    match value:
        case None:
            return None
        case bool() | np.bool_():
            msg = f"{value!r} is a bool, not a number: write it as text."
            raise CtzdError(msg)
        case float() | np.floating():
            plain = float(value)
            return None if math.isnan(plain) else plain
        case int() | np.integer():
            return int(value)
        case _:
            msg = f"ctzd.display shows floats and ints, not {type(value).__name__}."
            raise CtzdError(msg)


def number(value: Value, *, decimals: int | None = None, thousands: bool = True, abbreviate: Abbreviation | None = None, negative: Negative = "minus") -> str:
    """A number as the `Num` component shows it: `number(1234.5)` is `1,234.50`.

    A float shows 2 decimals and an int none, unless `decimals` (0 to 30) says otherwise; `abbreviate` divides by a
    thousand, million, or billion, shows the suffix, and defaults to 1 decimal: `number(1_250_000.0, abbreviate="M")`
    is `1.3M`. `thousands=False` drops the commas.
    """
    settings = _Settings(decimals=decimals, thousands=thousands, abbreviate=abbreviate, negative=negative)
    plain = _plain(value)
    return NULL_TEXT if plain is None else settings.text(plain)


def percent(value: Value, *, decimals: int | None = None, thousands: bool = True, negative: Negative = "minus") -> str:
    """A fraction as a percentage, as the `Pct` component shows it: `percent(0.0525)` is `5.25%`.

    The decimal point moves exactly, so the shown digits are the fraction's own. 2 decimals unless `decimals` says
    otherwise, for floats and ints alike.
    """
    settings = _Settings(decimals=decimals, thousands=thousands, percent=True, negative=negative)
    plain = _plain(value)
    return NULL_TEXT if plain is None else settings.text(plain)


def bps(value: Value, *, decimals: int | None = None, thousands: bool = True, abbreviate: Abbreviation | None = None, negative: Negative = "minus") -> str:
    """A fraction in basis points, as the `Bps` component shows it: `bps(0.0525)` is `525.00 bp`.

    A float is multiplied by 10,000 as a float, as the component does, then shows 2 decimals unless `decimals` says
    otherwise: `bps(0.0525, decimals=0)` is `525 bp`. An int is multiplied exactly and shows none. A no-break space
    keeps the unit beside the digits, and a negative in parentheses keeps it inside them: `(25.00 bp)`.
    """
    settings = _Settings(decimals=decimals, thousands=thousands, abbreviate=abbreviate, negative=negative)
    plain = _plain(value)
    if plain is None:
        return NULL_TEXT
    text = settings.text(plain * _BPS_PER_WHOLE)
    return f"{text[:-1]}{_BPS_UNIT})" if text.endswith(")") else f"{text}{_BPS_UNIT}"


def money(value: Value, currency: str, *, decimals: int | None = None, abbreviate: Abbreviation | None = None, negative: Negative = "minus") -> str:
    """An amount in a currency, as the `Money` component shows it: `money(-1234.5, "USD")` is `-$1,234.50`.

    `currency` is a three-letter code, in any case. The amount shows the currency's minor units (2 for USD and EUR,
    0 for JPY) unless `decimals` says otherwise or `abbreviate` is set, which shows 1. The symbol follows the sign:
    `($1,234.50)`. A symbol that ends in a letter, such as `CHF`, is followed by a no-break space. A well-formed code
    without a symbol is written as itself; anything else raises `CtzdError`.
    """
    symbol, minor_units = _currency(currency)
    places = decimals if decimals is not None else (minor_units if abbreviate is None else None)
    settings = _Settings(decimals=places, abbreviate=abbreviate, negative=negative)
    plain = _plain(value)
    if plain is None:
        return NULL_TEXT
    text = settings.text(plain)
    mark = f"{symbol}\N{NO-BREAK SPACE}" if symbol[-1].isalpha() else symbol
    sign = text[0] if text[0] in "-(" else ""
    return f"{sign}{mark}{text[len(sign) :]}"


def _currency(code: str) -> tuple[str, int]:
    if _CURRENCY_CODE.fullmatch(code) is None:
        msg = f"{code!r} is not a currency code: name one by its three letters, such as USD."
        raise CtzdError(msg)
    upper = code.upper()
    return _CURRENCIES.get(upper, (upper, 2))


def price32(value: Value, *, precision: Precision = "eighths", rounding: Rounding = "half-away-from-zero") -> str:
    """A Treasury price in 32nds, as the `Price32` component shows it: `price32(99.515625)` is `99-16+`.

    `99-16` is 99 and 16/32, `+` adds half a 32nd, and a third digit counts eighths of a 32nd, so `99-162` is
    99.5078125. The price is rounded to the `precision`'s step by `rounding`; a float is first read as its shortest
    decimal, as `repr` writes it, rounded to 8 decimals the same way. An infinite price shows `UNDEFINED_TEXT`.
    """
    steps_per_point = _THIRTY_SECONDS_PER_POINT * _checked(_STEPS_PER_THIRTY_SECOND, "precision", precision)
    rounds_away = _checked(_ROUNDS_AWAY, "rounding", rounding)
    plain = _plain(value)
    if plain is None:
        return NULL_TEXT
    if isinstance(plain, int):
        unscaled, scale = plain, 0
    elif math.isinf(plain):
        return UNDEFINED_TEXT
    else:
        unscaled, scale = _rescaled(*_shortest_decimal(plain), _PRICE_SCALE, rounds_away), _PRICE_SCALE
    steps = _rescaled(unscaled * steps_per_point, scale, 0, rounds_away)
    return _eighths_text(steps * (_EIGHTHS_PER_POINT // steps_per_point))


def _shortest_decimal(value: float) -> tuple[int, int]:
    """A finite float's shortest round-trip decimal, as an unscaled integer and a scale of 0 or more."""
    mantissa, _, exponent = repr(value).partition("e")
    whole, _, fraction = mantissa.partition(".")
    unscaled = int(whole + fraction)
    scale = len(fraction) - int(exponent or "0")
    if scale >= 0:
        return unscaled, scale
    return unscaled * _power(10, -scale), 0


def _rescaled(unscaled: int, scale: int, target: int, rounds_away: _RoundsAway) -> int:
    """`unscaled * 10**-scale` as an unscaled integer at `target` decimals, rounded by a rounding mode when that drops
    digits."""
    if scale <= target:
        return unscaled * _power(10, target - scale)
    return _divide_rounded(unscaled, _power(10, scale - target), rounds_away)


def _divide_rounded(numerator: int, divisor: int, rounds_away: _RoundsAway) -> int:
    """`numerator / divisor` rounded to an integer by a rounding mode; `divisor` is positive."""
    negative = numerator < 0
    quotient, remainder = divmod(abs(numerator), divisor)
    if remainder != 0:
        twice = 2 * remainder
        half: _Half = "tie" if twice == divisor else "below" if twice < divisor else "above"
        if rounds_away(negative, half, quotient % 2 == 1):
            quotient += 1
    return -quotient if negative else quotient


def _eighths_text(eighths: int) -> str:
    """A price held as a count of 256ths of a point, in 32nds."""
    sign = "-" if eighths < 0 else ""
    points, rest = divmod(abs(eighths), _EIGHTHS_PER_POINT)
    ticks, eighth = divmod(rest, _EIGHTHS_PER_THIRTY_SECOND)
    mark = "" if eighth == 0 else "+" if eighth == _HALF_IN_EIGHTHS else str(eighth)
    return f"{sign}{points}-{ticks:02d}{mark}"
