"""A number written as text, as the page's grid writes it: 15 significant digits half away from zero, then the shown
decimals."""

import pytest

from ctzd_common import display
from ctzd_common.display import Format, FormatError

NBSP = "\N{NO-BREAK SPACE}"
INF = float("inf")
NAN = float("nan")


@pytest.mark.parametrize(
    ("shown", "expected"),
    [
        (display.number(1234.5), "1,234.50"),
        (display.number(2.675), "2.68"),
        (display.number(1.005), "1.01"),
        (display.number(0.1 + 0.2), "0.30"),
        (display.number(42.0, decimals=0), "42"),
        (display.number(0.5, decimals=0), "1"),
        (display.number(-0.5, decimals=0), "-1"),
        (display.number(5), "5.00"),
        (display.number(1234567.891), "1,234,567.89"),
        (display.number(123456789012345.67), "123,456,789,012,346.00"),
        (display.number(0.123456789, decimals=6), "0.123457"),
    ],
)
def test_numbers(shown: str, expected: str) -> None:
    assert shown == expected


@pytest.mark.parametrize(
    ("shown", "expected"),
    [
        (display.percent(0.0525), "5.25%"),
        (display.percent(0.035, decimals=0), "4%"),
        (display.percent(-0.0525, decimals=1), "-5.3%"),
        (display.percent(1.0), "100.00%"),
        (display.bps(0.0525, decimals=0), f"525{NBSP}bp"),
        (display.bps(0.00125), f"12.50{NBSP}bp"),
        (display.bps(-0.000015, decimals=1), f"-0.2{NBSP}bp"),
        (display.price(101.25, decimals=3), "101.250"),
        (display.price(99.84375), "99.84"),
    ],
)
def test_rates_spreads_and_prices(shown: str, expected: str) -> None:
    assert shown == expected


@pytest.mark.parametrize(
    ("currency", "expected"),
    [
        ("USD", "-$1,234.50"),
        ("EUR", "-€1,234.50"),
        ("GBP", "-£1,234.50"),
        ("JPY", "-¥1,235"),
        ("CHF", f"-CHF{NBSP}1,234.50"),
        ("CAD", "-CA$1,234.50"),
        ("AUD", "-A$1,234.50"),
        ("HKD", "-HK$1,234.50"),
        ("SGD", "-S$1,234.50"),
        ("CNY", "-CN¥1,234.50"),
        ("INR", "-₹1,234.50"),
        ("SEK", f"-SEK{NBSP}1,234.50"),
        ("NOK", f"-NOK{NBSP}1,234.50"),
        ("DKK", f"-DKK{NBSP}1,234.50"),
    ],
)
def test_money_in_each_currency_to_its_minor_units(currency: str, expected: str) -> None:
    assert display.money(-1234.5, currency) == expected


def test_money_with_its_own_decimals() -> None:
    assert display.money(1_250_000.0, "USD", decimals=0) == "$1,250,000"
    assert display.money(1234.5, "JPY", decimals=2) == "¥1,234.50"
    assert display.money(0.005, "GBP") == "£0.01"


@pytest.mark.parametrize(
    ("value", "step", "expected"),
    [
        (99.515625, "32nd", "99-17"),
        (99.5, "32nd", "99-16"),
        (100.0, "32nd", "100-00"),
        (99.99, "32nd", "100-00"),
        (99.515625, "half", "99-16+"),
        (99.5078125, "quarter", "99-162"),
        (99.5234375, "quarter", "99-166"),
        (99.50390625, "eighth", "99-161"),
        (99.51171875, "eighth", "99-163"),
        (-99.5, "32nd", "-99-16"),
        (-0.001, "32nd", "0-00"),
    ],
)
def test_prices_in_32nds_by_step(value: float, step: display.Step, expected: str) -> None:
    assert display.price32(value, step=step) == expected


@pytest.mark.parametrize("kind", ["number", "pct", "bps", "price", "price32"])
def test_a_missing_value_is_empty_text(kind: display.Kind) -> None:
    assert display.text(None, Format(kind)) == ""
    assert display.text(NAN, Format(kind)) == ""
    assert display.money(None, "USD") == ""
    assert display.money(NAN, "USD") == ""


@pytest.mark.parametrize("value", [-0.0, -0.0001, -0.004999, -1e-300])
def test_a_value_that_rounds_to_zero_has_no_minus_sign(value: float) -> None:
    assert display.number(value) == "0.00"
    assert display.percent(value / 1000) == "0.00%"
    assert display.money(value, "USD") == "$0.00"


def test_rounding_to_zero_keeps_the_sign_once_it_is_not_zero() -> None:
    assert display.number(-0.005) == "-0.01"


@pytest.mark.parametrize(("value", "expected"), [(INF, "∞"), (-INF, "-∞")])
def test_infinities(value: float, expected: str) -> None:
    assert display.number(value) == expected
    assert display.percent(value) == expected
    assert display.price32(value) == expected


def test_text_without_a_format_is_a_number_to_two_decimals() -> None:
    assert display.text(1234.567) == "1,234.57"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kind": "money"}, "currency None is not one ctzd knows"),
        ({"kind": "money", "currency": "XYZ"}, "currency 'XYZ'"),
        ({"kind": "number", "currency": "USD"}, "only a money format takes a currency"),
        ({"kind": "price", "step": "half"}, "only a price in 32nds takes a step"),
        ({"kind": "number", "decimals": 16}, "decimals must be 0 to 15"),
        ({"kind": "number", "decimals": -1}, "decimals must be 0 to 15"),
    ],
)
def test_a_format_that_cannot_be_says_what_to_give(kwargs: dict[str, str | int], message: str) -> None:
    with pytest.raises(FormatError, match=message):
        Format(**kwargs)  # ty: ignore[invalid-argument-type] - wrong values are the point


@pytest.mark.parametrize(
    ("given", "spec"),
    [
        (Format(), {"decimals": 2}),
        (Format("pct", 1), {"decimals": 1, "percent": True}),
        (Format("bps", 0), {"decimals": 0, "bps": True}),
        (Format("money", currency="JPY"), {"decimals": 0, "symbol": "¥"}),
        (Format("money", 2, currency="EUR"), {"decimals": 2, "symbol": "€"}),
        (Format("price32"), {"thirtySeconds": 1}),
        (Format("price32", step="eighth"), {"thirtySeconds": 8}),
    ],
)
def test_the_browsers_formatter_gets_every_default_resolved(given: Format, spec: dict[str, object]) -> None:
    assert given.spec() == spec
