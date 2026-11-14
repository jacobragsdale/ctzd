"""Every loader answers its sample rows, on the fakes, with the network blocked, as `ctzd kit check` runs it."""

from ctzd_kit import contract


def test_the_kit_meets_the_contract() -> None:
    assert contract.check("{{kit}}")["problems"] == []
