"""`python -m ctzd.explore`: what an agent sees of a source before it writes a loader."""

import json

import pytest

import ctzd
from ctzd import explore


@pytest.mark.parametrize("table", ["prices; DROP TABLE x", 'FI."PRICES"', "a.b.c", "[dbo].[trades]"])
def test_a_table_that_is_not_a_plain_name_is_refused(table: str) -> None:
    with pytest.raises(ctzd.SourceError, match="not a table name"):
        explore.split_table(table)


def test_schema_and_table_are_split() -> None:
    assert explore.split_table("FI.BOND_PRICES") == ("FI", "BOND_PRICES")
    assert explore.split_table("trades") == (None, "trades")


def test_a_rest_source_says_it_has_no_tables(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source = {"kind": "rest", "base_url": "https://ratings.contoso.com", "auth": {"type": "none"}, "description": "Bond ratings by ISIN"}
    monkeypatch.setenv("CTZD_SOURCE_RATINGS_API", json.dumps(source))
    assert explore.main(["ratings_api"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["kind"] == "rest"
    assert printed["description"] == "Bond ratings by ISIN"
    assert "no tables" in printed["message"]


def test_a_source_the_app_cannot_read_is_an_error_in_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("CTZD_SOURCE_FI_PRICING", raising=False)
    assert explore.main(["fi_pricing"]) == 1
    assert "is not configured" in json.loads(capsys.readouterr().out)["error"]
