"""Fake rows in fixtures/SOURCE/NAME.csv: what `ctzd dev --fake` serves and tests may read."""

from pathlib import Path

import pytest

import ctzd


def fixtures(root: Path) -> Path:
    folder = root / "fixtures" / "fi_pricing"
    folder.mkdir(parents=True)
    (folder / "prices.csv").write_text("isin,as_of,price,bonds,trade_id\nUS0001,2026-11-06,99.5,3,1001\nUS0002,2026-11-06,101.25,,1002\n", encoding="utf-8")
    return root / "fixtures"


def test_each_csv_answers_its_sql_file_with_numbers_as_floats_and_dates_as_dates(tmp_path: Path) -> None:
    with ctzd.testing.fake_sources(**ctzd.testing.fixture_sources(fixtures(tmp_path))):
        prices = ctzd.query("fi_pricing", "sql/prices.sql")
    assert prices["price"].tolist() == [99.5, 101.25]
    assert prices["bonds"].dtype == "float64"
    assert str(prices["as_of"].dtype).startswith("datetime64")


def test_an_id_column_stays_text_as_the_query_selects_it(tmp_path: Path) -> None:
    with ctzd.testing.fake_sources(**ctzd.testing.fixture_sources(fixtures(tmp_path))):
        prices = ctzd.query("fi_pricing", "sql/prices.sql")
    assert prices["trade_id"].tolist() == ["1001", "1002"]


def test_serving_fixtures_fakes_every_source_and_refuses_the_rest(tmp_path: Path) -> None:
    folder = fixtures(tmp_path)
    with ctzd.testing.fake_sources():  # puts the real sources back afterwards
        ctzd.testing.serve_fixtures(folder)
        assert len(ctzd.query("fi_pricing", "sql/prices.sql")) == 2
        with pytest.raises(ctzd.SourceError, match="no fake for source 'fi_trades'"):
            ctzd.query("fi_trades", "sql/trades.sql")


def test_a_missing_folder_has_no_fixtures(tmp_path: Path) -> None:
    assert ctzd.testing.fixture_sources(tmp_path / "fixtures") == {}


def test_a_number_written_with_a_leading_zero_stays_text(tmp_path: Path) -> None:
    folder = tmp_path / "fixtures" / "fi_pricing"
    folder.mkdir(parents=True)
    (folder / "accounts.csv").write_text("account,sedol,balance\n00123456,0263494,10\n00123457,B0YBKJ7,-2.5\n", encoding="utf-8")
    with ctzd.testing.fake_sources(**ctzd.testing.fixture_sources(tmp_path / "fixtures")):
        accounts = ctzd.query("fi_pricing", "sql/accounts.sql")
    assert accounts["account"].tolist() == ["00123456", "00123457"]
    assert accounts["sedol"].tolist() == ["0263494", "B0YBKJ7"]
    assert accounts["balance"].tolist() == [10.0, -2.5]


def test_the_loaders_require_columns_types_the_fixture(tmp_path: Path) -> None:
    folder = tmp_path / "fixtures" / "fi_pricing"
    folder.mkdir(parents=True)
    (folder / "bonds.csv").write_text("sedol,cusip,price\n2046251,912828,99.5\n", encoding="utf-8")
    data = tmp_path / "src" / "app" / "data"
    data.mkdir(parents=True)
    (data / "loaders.py").write_text(
        "import ctzd\n\n\ndef load_bonds():\n"
        '    frame = ctzd.query("fi_pricing", "sql/bonds.sql", {"as_of": 1})\n'
        '    return ctzd.require_columns(frame, {"sedol": "str", "cusip": "str", "price": "float"})\n',
        encoding="utf-8",
    )
    with ctzd.testing.fake_sources(**ctzd.testing.fixture_sources(tmp_path / "fixtures")):
        bonds = ctzd.query("fi_pricing", "sql/bonds.sql")
    assert bonds["sedol"].tolist() == ["2046251"]
    assert bonds["cusip"].tolist() == ["912828"]
    assert bonds["price"].tolist() == [99.5]


def test_a_rest_fixture_answers_its_routes_when_served(tmp_path: Path) -> None:
    folder = tmp_path / "fixtures" / "fi_api"
    folder.mkdir(parents=True)
    (folder / "rest.json").write_text('{"GET /bonds": {"items": [{"isin": "US0001", "price": 99.5}]}}', encoding="utf-8")
    with ctzd.testing.fake_sources():
        ctzd.testing.serve_fixtures(tmp_path / "fixtures")
        bonds = ctzd.rest("fi_api").get_frame("/bonds", records="items")
    assert bonds.to_dict("records") == [{"isin": "US0001", "price": 99.5}]


def test_a_rest_fixture_must_be_keyed_by_request(tmp_path: Path) -> None:
    folder = tmp_path / "fixtures" / "fi_api"
    folder.mkdir(parents=True)
    (folder / "rest.json").write_text('[{"isin": "US0001"}]', encoding="utf-8")
    with pytest.raises(ctzd.SourceError, match="keyed by request"):
        ctzd.testing.fixture_sources(tmp_path / "fixtures")
