"""Fake rows in fixtures/SOURCE/NAME.csv: what `ctzd dev --fake` serves and tests may read."""

from pathlib import Path

import pytest

import ctzd


def fixtures(root: Path) -> Path:
    folder = root / "fixtures" / "fi_pricing"
    folder.mkdir(parents=True)
    (folder / "prices.csv").write_text("isin,as_of,price,bonds\nUS0001,2026-11-06,99.5,3\nUS0002,2026-11-06,101.25,\n", encoding="utf-8")
    return root / "fixtures"


def test_each_csv_answers_its_sql_file_with_numbers_as_floats_and_dates_as_dates(tmp_path: Path) -> None:
    with ctzd.testing.fake_sources(**ctzd.testing.fixture_sources(fixtures(tmp_path))):
        prices = ctzd.query("fi_pricing", "sql/prices.sql")
    assert prices["price"].tolist() == [99.5, 101.25]
    assert prices["bonds"].dtype == "float64"
    assert str(prices["as_of"].dtype).startswith("datetime64")


def test_serving_fixtures_fakes_every_source_and_refuses_the_rest(tmp_path: Path) -> None:
    folder = fixtures(tmp_path)
    with ctzd.testing.fake_sources():  # puts the real sources back afterwards
        ctzd.testing.serve_fixtures(folder)
        assert len(ctzd.query("fi_pricing", "sql/prices.sql")) == 2
        with pytest.raises(ctzd.SourceError, match="no fake for source 'fi_trades'"):
            ctzd.query("fi_trades", "sql/trades.sql")


def test_a_missing_folder_has_no_fixtures(tmp_path: Path) -> None:
    assert ctzd.testing.fixture_sources(tmp_path / "fixtures") == {}
