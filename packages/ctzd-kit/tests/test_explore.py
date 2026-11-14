"""What `ctzd sources` shows of a kit, and the department's skill written from the same facts."""

import inspect
import json
import os
from datetime import date, datetime

import pandas as pd
import pytest

from ctzd_common import samples
from ctzd_kit import SourceError, explore, testing
from ctzd_kit.skill import render
from ctzd_kit.sources import load_kit

FI = load_kit("ctzd_fi")
MAS = load_kit("ctzd_mas")


@pytest.fixture(autouse=True)
def desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop with nothing faked, no source's secret, and no sample rows served yet."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    samples.reset()


def as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return {str(key): item for key, item in value.items()}


def as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return list(value)


def by_name(described: dict[str, object]) -> dict[str, dict[str, object]]:
    sources = [as_dict(source) for source in as_list(described["sources"])]
    return {str(source["name"]): source for source in sources}


def test_describe_lists_each_source_and_loader_with_its_columns_and_an_example() -> None:
    with testing.fakes():
        described = explore.describe(FI)
    assert (described["kit"], described["sbu"], described["title"], described["io"]) == ("ctzd_fi", "fi", "Fixed Income", ["fi_risklib"])
    sources = by_name(described)
    assert list(sources) == ["sample", "fi_curves", "fi_pricing", "fi_risk"]
    pricing = sources["fi_pricing"]
    assert (pricing["kind"], pricing["dialect"], pricing["credentials"], pricing["serves_sample"]) == ("oracle", "oracle", False, "every source serves sample rows here")
    (prices,) = [as_dict(found) for found in as_list(pricing["loaders"])]
    assert prices["signature"] == "prices(as_of: datetime.date) -> pandas.DataFrame"
    assert prices["example"] == "fi_pricing.prices(as_of=date(2026, 11, 13))"
    assert {column["name"]: column["type"] for column in map(as_dict, as_list(prices["columns"]))} == {
        "isin": "text",
        "issuer": "text",
        "sector": "text",
        "clean_price": "number",
        "ytw": "number",
        "oas": "number",
        "as_of": "date",
    }


def test_describe_says_why_each_source_serves_sample_rows_off_the_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_SOURCE_FI_PRICING", "{}")
    sources = by_name(explore.describe(FI))
    assert sources["fi_pricing"]["serves_sample"] is None
    assert sources["fi_pricing"]["credentials"] is True
    assert sources["fi_curves"]["serves_sample"] == "it has no credentials here"
    assert sources["sample"]["serves_sample"] == "it exists only as sample rows"
    with testing.given({"CTZD_ENV": "prod"}):
        prod = by_name(explore.describe(FI))
    assert "no credentials in prod" in str(prod["fi_curves"]["serves_sample"])


def test_the_skill_carries_every_source_loader_and_the_notes() -> None:
    with testing.fakes():
        skill = str(explore.describe(MAS)["skill"])
    assert skill.startswith("---\nname: sbu-mas\n")
    assert "## mas_trades" in skill
    assert "Your own SQL for it is tsql SQL in `src/app/data/sql/`." in skill
    assert "## mas_reports" in skill
    assert "It cannot run on a desktop, so there it always serves its sample rows." in skill
    assert "### `mas_trades.trades(start: datetime.date, end: datetime.date) -> pandas.DataFrame`" in skill
    assert "Example: `mas_trades.trades(start=date(2026, 11, 13), end=date(2026, 11, 13))`" in skill
    assert "`portfolio_id` (text)" in skill
    assert MAS.notes().strip().splitlines()[0] in skill
    assert f"## {MAS.spec.title}'s notes" in skill


def test_the_skill_tolerates_sources_described_partly() -> None:
    skill = render(FI, [{"name": "fi_odd", "description": "Odd", "kind": "package", "loaders": ["not a loader", {"signature": "x()", "columns": "none"}]}])
    assert "### `fi_odd.x()`" in skill
    assert "Columns: ." in skill


def test_a_loader_without_sample_rows_shows_why_in_its_columns() -> None:
    with testing.fakes():
        (described,) = explore.loaders_of(FI.source("fi_pricing"))
        shown = explore.describe_loader(FI.source("fi_curves"), described)
    assert shown["columns"] == [
        {"name": "?", "type": "fi_curves has no sample rows for prices: write a few made-up rows to fixtures/fi_curves/prices.csv, one column per column the real read returns."}
    ]


@pytest.mark.parametrize(
    ("annotation", "example"), [(date, "date(2026, 11, 13)"), ("date", "date(2026, 11, 13)"), (datetime, "datetime(2026, 11, 13, 7, 0)"), (int, "1"), (float, "1.0"), (bool, "True"), (str, "'book'")]
)
def test_example_values_by_annotation(annotation: object, example: str) -> None:
    assert explore.example_value(inspect.Parameter("book", inspect.Parameter.KEYWORD_ONLY, annotation=annotation)) == example


def test_column_types() -> None:
    frame = pd.DataFrame({"b": [True], "d": pd.to_datetime(["2026-11-13"]), "n": [1.5], "t": ["x"]})
    assert [explore.column_type(frame[name]) for name in frame.columns] == ["bool", "date", "number", "text"]


@pytest.mark.parametrize("table", ["prices; DROP TABLE x", 'FI."PRICES"', "a.b.c", "[dbo].[trades]", "", "fi..prices"])
def test_a_table_that_is_not_a_plain_name_is_refused(table: str) -> None:
    with pytest.raises(SourceError, match="not a table name"):
        explore.split_table(table)


def test_schema_and_table_are_split() -> None:
    assert explore.split_table("FI.BOND_PRICES") == ("FI", "BOND_PRICES")
    assert explore.split_table("trades") == (None, "trades")


def test_a_source_that_is_not_a_database_has_no_tables() -> None:
    found = explore.tables(FI.source("fi_curves"), None)
    assert found["kind"] == "rest"
    assert "no tables" in str(found["message"])


def test_main_prints_one_json_object(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(explore, "installed", lambda: FI)
    with testing.fakes():
        assert explore.main(["describe"]) == 0
    assert json.loads(capsys.readouterr().out)["kit"] == "ctzd_fi"
    assert explore.main(["tables", "fi_curves"]) == 0
    assert "no tables" in json.loads(capsys.readouterr().out)["message"]


def test_main_answers_errors_in_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(explore, "installed", lambda: FI)
    assert explore.main(["tables", "fi_trades"]) == 1
    assert "has no source 'fi_trades'" in json.loads(capsys.readouterr().out)["error"]
    assert explore.main(["table", "fi_pricing", "FI.BOND_PRICES"]) == 1
    assert "CTZD_SOURCE_FI_PRICING is not set" in json.loads(capsys.readouterr().out)["error"]
    assert explore.main(["nonsense"]) == 2


def test_two_kits_installed_together_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert explore.main(["describe"]) == 1
    assert "exactly one kit installed, and here there are 2: ctzd_fi, ctzd_mas" in json.loads(capsys.readouterr().out)["error"]
