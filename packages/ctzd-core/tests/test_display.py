"""`ctzd.display` shows a number as basis-ui's grid and number components do.

The fixture cases are parallax's own: its Rust formatter writes them, and basis-ui's TypeScript port is tested against
the same file. The focused cases follow basis-ui's tests of the components' text (`tests/number-components.test.ts`).
"""

import math
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import pytest
from pydantic import BaseModel, ConfigDict, Field

import ctzd
from ctzd.display import NULL_TEXT, UNDEFINED_TEXT, Abbreviation, Negative, bps, money, number, percent, price32

FIXTURES = Path(__file__).parent / "fixtures" / "format.json"
NBSP = "\N{NO-BREAK SPACE}"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class _Source(_Model):
    repository: str
    path: str
    commit: str
    kept: str


class _Format(_Model):
    abbreviate: Abbreviation | None
    decimals: int | None
    negative: Negative
    percent: bool
    precision: str
    thousands: bool | None
    zone: str | None


class _FloatCase(_Model):
    kind: Literal["float"]
    text: str
    value: float | Literal["NaN", "inf", "-inf"]  # JSON holds no NaN or infinity, so those travel as text


class _IntegerCase(_Model):
    kind: Literal["integer"]
    text: str
    value: str  # an i64 as text, since a JSON number past 2**53 would lose digits in other readers


class _Group(_Model):
    format: _Format
    cases: list[Annotated[_FloatCase | _IntegerCase, Field(discriminator="kind")]]


class _Fixtures(_Model):
    source: _Source
    groups: list[_Group]


def _label(shown: _Format) -> str:
    parts = [
        *(["percent"] if shown.percent else []),
        *([f"decimals={shown.decimals}"] if shown.decimals is not None else []),
        *([f"abbreviate={shown.abbreviate}"] if shown.abbreviate is not None else []),
        *(["thousands=False"] if shown.thousands is False else []),
        *([f"negative={shown.negative}"] if shown.negative != "minus" else []),
    ]
    return " ".join(parts) or "default"


def _fixture_cases() -> list[pytest.ParameterSet]:
    fixtures = _Fixtures.model_validate_json(FIXTURES.read_bytes())
    cases: list[pytest.ParameterSet] = []
    for group in fixtures.groups:
        for case in group.cases:
            value: float | int = float(case.value) if isinstance(case, _FloatCase) else int(case.value)
            # parallax shows a NaN double as its text for an undefined result. In pandas a NaN marks a missing value,
            # which Arrow stores, and so the grid shows, as null, so ctzd.display shows NaN as the text for null.
            text = NULL_TEXT if isinstance(value, float) and math.isnan(value) else case.text
            cases.append(pytest.param(group.format, value, text, id=f"{_label(group.format)}: {value!r}"))
    return cases


@pytest.mark.parametrize(("shown", "value", "text"), _fixture_cases())
def test_matches_the_rust_formatter(shown: _Format, value: float, text: str) -> None:
    thousands = True if shown.thousands is None else shown.thousands
    if shown.percent:
        assert percent(value, decimals=shown.decimals, thousands=thousands, negative=shown.negative) == text
    else:
        assert number(value, decimals=shown.decimals, thousands=thousands, abbreviate=shown.abbreviate, negative=shown.negative) == text


@pytest.mark.parametrize(
    ("shown", "text"),
    [
        pytest.param(number(1234.5), "1,234.50", id="a float shows two decimals"),
        pytest.param(number(1234), "1,234", id="an int shows none"),
        pytest.param(number(2.675, decimals=2), "2.68", id="2.675 rounds at 15 digits first, so up"),
        pytest.param(number(0.1 + 0.2), "0.30", id="0.1 + 0.2"),
        pytest.param(number(0.1 + 0.2, decimals=17), "0.30000000000000000", id="0.1 + 0.2 at full precision"),
        pytest.param(number(-0.0001, decimals=2), "0.00", id="a negative that rounds to zero has no sign"),
        pytest.param(number(-0.0001, decimals=2, negative="parentheses"), "0.00", id="nor parentheses"),
        pytest.param(number(-12.5, negative="parentheses"), "(12.50)", id="in parentheses"),
        pytest.param(number(1_234_567, thousands=False), "1234567", id="without commas"),
        pytest.param(number(1_500_000.0, abbreviate="M"), "1.5M", id="abbreviated to one decimal"),
        pytest.param(number(-1_249_999.0, abbreviate="M"), "-1.2M", id="abbreviated, rounded once"),
        pytest.param(number(12_345_678_901_234_567_890.0, decimals=0), "12,345,678,901,234,600,000", id="digits past the 15th show as zeros"),
        pytest.param(number(2**60), "1,152,921,504,606,846,976", id="an int keeps every digit"),
        pytest.param(number(math.inf), "\N{INFINITY}", id="infinity"),
        pytest.param(number(-math.inf, negative="parentheses"), "(\N{INFINITY})", id="negative infinity in parentheses"),
        pytest.param(number(None), NULL_TEXT, id="None"),
        pytest.param(number(math.nan), NULL_TEXT, id="NaN"),
        pytest.param(number(np.float64(2.675), decimals=2), "2.68", id="a numpy float64"),
        pytest.param(number(np.float32(0.5)), "0.50", id="a numpy float32"),
        pytest.param(number(np.int64(-1234)), "-1,234", id="a numpy int64"),
        pytest.param(number(np.float64("nan")), NULL_TEXT, id="a numpy NaN"),
    ],
)
def test_number(shown: str, text: str) -> None:
    assert shown == text


@pytest.mark.parametrize(
    ("shown", "text"),
    [
        pytest.param(percent(0.0525), "5.25%", id="a fraction"),
        pytest.param(percent(-0.001), "-0.10%", id="a negative fraction"),
        pytest.param(percent(0.000_05), "0.01%", id="the point moves exactly before rounding"),
        pytest.param(percent(-0.000_04), "0.00%", id="a negative that rounds to zero has no sign"),
        pytest.param(percent(0.0525, decimals=0), "5%", id="no decimals"),
        pytest.param(percent(-0.0525, negative="parentheses"), "(5.25%)", id="in parentheses"),
        pytest.param(percent(5), "500.00%", id="an int is a fraction too, at two decimals"),
        pytest.param(percent(math.inf), "\N{INFINITY}", id="infinity has no unit"),
        pytest.param(percent(None), NULL_TEXT, id="None"),
    ],
)
def test_percent(shown: str, text: str) -> None:
    assert shown == text


@pytest.mark.parametrize(
    ("shown", "text"),
    [
        pytest.param(bps(0.0025), f"25.00{NBSP}bp", id="a float shows two decimals"),
        pytest.param(bps(0.0525, decimals=0), f"525{NBSP}bp", id="no decimals"),
        pytest.param(bps(0.00125), f"12.50{NBSP}bp", id="a fraction"),
        pytest.param(bps(-0.0025, negative="parentheses"), f"(25.00{NBSP}bp)", id="the unit sits inside the parentheses"),
        pytest.param(bps(1), f"10,000{NBSP}bp", id="an int is multiplied exactly"),
        pytest.param(bps(-math.inf), f"-\N{INFINITY}{NBSP}bp", id="negative infinity"),
        pytest.param(bps(math.nan), NULL_TEXT, id="NaN"),
    ],
)
def test_bps(shown: str, text: str) -> None:
    assert shown == text


@pytest.mark.parametrize(
    ("shown", "text"),
    [
        pytest.param(money(1234.5, "USD"), "$1,234.50", id="dollars"),
        pytest.param(money(-1234.5, "USD"), "-$1,234.50", id="the symbol follows the minus sign"),
        pytest.param(money(-1234.5, "USD", negative="parentheses"), "($1,234.50)", id="and sits inside parentheses"),
        pytest.param(money(1234.5, "EUR"), "\N{EURO SIGN}1,234.50", id="euros"),
        pytest.param(money(1234.0, "JPY"), "\N{YEN SIGN}1,234", id="yen have no minor units"),
        pytest.param(money(1.2345, "KWD"), f"KWD{NBSP}1.235", id="dinars have three, rounded at 15 digits first"),
        pytest.param(money(1234.5, "CHF"), f"CHF{NBSP}1,234.50", id="a symbol of letters takes a no-break space"),
        pytest.param(money(-1234.5, "CHF", negative="parentheses"), f"(CHF{NBSP}1,234.50)", id="inside parentheses too"),
        pytest.param(money(1234.5, "usd"), "$1,234.50", id="a code in any case"),
        pytest.param(money(1234.5, "XYZ"), f"XYZ{NBSP}1,234.50", id="a code without a symbol is written as itself"),
        pytest.param(money(1_500_000.0, "USD", abbreviate="M"), "$1.5M", id="abbreviated to one decimal"),
        pytest.param(money(1234.5678, "USD", decimals=4), "$1,234.5678", id="decimals override the minor units"),
        pytest.param(money(-math.inf, "USD"), "-$\N{INFINITY}", id="negative infinity"),
        pytest.param(money(None, "USD"), NULL_TEXT, id="None"),
    ],
)
def test_money(shown: str, text: str) -> None:
    assert shown == text


@pytest.mark.parametrize(
    ("shown", "text"),
    [
        pytest.param(price32(99.515625), "99-16+", id="a half 32nd is +"),
        pytest.param(price32(99.5078125), "99-162", id="a third digit counts eighths"),
        pytest.param(price32(-0.140625), "-0-04+", id="a negative change"),
        pytest.param(price32(99.515625, precision="thirty-seconds"), "99-17", id="to whole 32nds"),
        pytest.param(price32(99.53, precision="halves"), "99-17", id="to half 32nds"),
        pytest.param(price32(99.53, precision="quarters", rounding="floor"), "99-166", id="to quarters, down"),
        pytest.param(price32(-99.99, precision="thirty-seconds", rounding="ceiling"), "-99-31", id="toward positive infinity"),
        pytest.param(price32(99.001953125), "99-001", id="half a 256th rounds away from zero"),
        pytest.param(price32(99.001953125, rounding="toward-zero"), "99-00", id="or truncates"),
        pytest.param(price32(99.001953125, rounding="half-even"), "99-00", id="or rounds to even"),
        pytest.param(price32(-0.001), "0-00", id="a negative that rounds to zero has no sign"),
        pytest.param(price32(100), "100-00", id="an int"),
        pytest.param(price32(math.inf), UNDEFINED_TEXT, id="infinity has no price"),
        pytest.param(price32(math.nan), NULL_TEXT, id="NaN"),
    ],
)
def test_price32(shown: str, text: str) -> None:
    assert shown == text


def _untyped(function: Callable[..., str]) -> Callable[..., str]:
    """The function as code without types calls it, such as code passing on a value it read from a frame."""
    return function


@pytest.mark.parametrize("value", [True, np.True_, "1.5", Decimal("1.5")], ids=repr)
def test_only_floats_and_ints_are_numbers(value: object) -> None:
    with pytest.raises(ctzd.CtzdError):
        _untyped(number)(value)


@pytest.mark.parametrize("decimals", [-1, 31])
def test_decimals_run_from_0_to_30_even_for_a_missing_value(decimals: int) -> None:
    with pytest.raises(ctzd.CtzdError, match="decimals"):
        number(None, decimals=decimals)


def test_an_option_the_function_does_not_offer_is_refused() -> None:
    with pytest.raises(ctzd.CtzdError, match="negative="):
        _untyped(number)(1.0, negative="brackets")
    with pytest.raises(ctzd.CtzdError, match="abbreviate="):
        _untyped(bps)(1.0, abbreviate="k")
    with pytest.raises(ctzd.CtzdError, match="precision="):
        _untyped(price32)(None, precision="sixty-fourths")
    with pytest.raises(ctzd.CtzdError, match="rounding="):
        _untyped(price32)(None, rounding="half-up")


@pytest.mark.parametrize("currency", ["DOLLARS", "US", "U$D", ""])
def test_a_currency_is_three_letters(currency: str) -> None:
    with pytest.raises(ctzd.CtzdError, match="currency code"):
        money(None, currency)


def test_apps_reach_it_from_the_package() -> None:
    assert ctzd.display.number(1234.5) == "1,234.50"
