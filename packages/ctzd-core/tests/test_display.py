"""`ctzd.display` writes numbers as the page does; basis-core's formatter is held to parallax's own cases."""

import pytest

import ctzd
from ctzd.display import Format

NBSP = "\N{NO-BREAK SPACE}"


@pytest.mark.parametrize(
    ("shown", "expected"),
    [
        (ctzd.display.number(1234.5), "1,234.50"),
        (ctzd.display.number(2.675, decimals=2), "2.68"),
        (ctzd.display.number(0.1 + 0.2), "0.30"),
        (ctzd.display.number(42.0, decimals=0), "42"),
        (ctzd.display.number(-0.0001, decimals=2), "0.00"),
        (ctzd.display.number(float("nan")), ""),
        (ctzd.display.number(None), ""),
        (ctzd.display.percent(0.0525), "5.25%"),
        (ctzd.display.bps(0.0525, decimals=0), f"525{NBSP}bp"),
        (ctzd.display.money(-1234.5, "USD"), "-$1,234.50"),
        (ctzd.display.price(101.25, decimals=3), "101.250"),
        (ctzd.display.price32(99.515625), "99-17"),
        (ctzd.display.price32(99.515625, step="half"), "99-16+"),
        (ctzd.display.text(1_250_000.0, Format(abbreviate="M")), "1.3M"),
        (ctzd.display.text(0.0525, {"kind": "pct", "decimals": 1}), "5.3%"),
    ],
)
def test_numbers_read_as_the_page_writes_them(shown: str, expected: str) -> None:
    assert shown == expected
