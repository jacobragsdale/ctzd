"""The seam: a loader serves its sample rows wherever the source cannot or need not be read, and otherwise runs and hands
back numbers as float64 and ids as text."""

import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ctzd_common import samples
from ctzd_fi import fi_curves, fi_pricing, fi_risk
from ctzd_kit import DataError, SourceError, app_query, loaders, query, sample, testing
from ctzd_kit.sources import load_kit
from ctzd_mas import mas_reports

DAY = date(2026, 11, 13)
FI = load_kit("ctzd_fi")
MAS = load_kit("ctzd_mas")
PRICING_CONNECTION = '{"kind": "oracle", "dsn": "db.contoso.com:1521/FIPRICE", "user": "fi_app", "password": "x"}'


@pytest.fixture(autouse=True)
def desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop with nothing faked, no source's secret, and no sample rows served yet."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    samples.reset()


def test_the_sample_source_serves_its_rows_even_in_prod() -> None:
    with testing.given({"CTZD_ENV": "prod"}):
        accounts = sample.accounts()
    assert accounts["account_id"].tolist() == ["A1", "A2", "A3"]
    assert accounts["closed"].tolist() == [False, False, True]
    assert samples.served() == {"sample"}


def test_with_fakes_every_source_serves_its_rows_and_is_marked() -> None:
    with testing.fakes():
        prices, measures, curve = fi_pricing.prices(DAY), fi_risk.measures(DAY), fi_curves.treasury_curve(DAY)
    assert prices["clean_price"].dtype == "float64"
    assert prices["as_of"].dtype.kind == "M"
    assert measures["dv01"].dtype == "float64"
    assert curve["tenor"].iloc[0] == "3M"
    assert samples.served() == {"fi_pricing", "fi_risk", "fi_curves"}


def test_a_source_that_cannot_run_on_a_desktop_serves_its_rows_there(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_SOURCE_MAS_REPORTS", '{"kind": "file", "url": "/nowhere"}')
    with testing.given({"CTZD_ENV": "local"}):
        allocations = mas_reports.allocations(DAY)
        assert loaders.sample_reason(MAS.source("mas_reports")) == "it cannot run on a desktop"
    assert allocations["portfolio_id"].iloc[0] == "P-BAL"
    with testing.given({"CTZD_ENV": "dev"}):
        assert loaders.sample_reason(MAS.source("mas_reports")) is None


@pytest.mark.parametrize("env", ["local", "dev"])
def test_without_credentials_outside_prod_a_source_serves_its_rows(env: str) -> None:
    with testing.given({"CTZD_ENV": env}):
        assert loaders.sample_reason(FI.source("fi_pricing")) == "it has no credentials here"
        assert fi_pricing.prices(DAY)["isin"].iloc[0] == "US91282CJL69"
    assert samples.served() == {"fi_pricing"}


def test_with_credentials_a_source_is_read_for_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_SOURCE_FI_PRICING", PRICING_CONNECTION)
    with testing.given({"CTZD_ENV": "dev"}):
        assert loaders.sample_reason(FI.source("fi_pricing")) is None


def test_in_prod_a_source_without_credentials_is_an_error() -> None:
    with testing.given({"CTZD_ENV": "prod"}), pytest.raises(SourceError, match="no credentials in prod"):
        fi_pricing.prices(DAY)
    assert samples.served() == frozenset()


def test_the_apps_own_fixtures_win_over_the_kits(tmp_path: Path) -> None:
    (tmp_path / "fi_pricing").mkdir()
    (tmp_path / "fi_pricing" / "prices.csv").write_text("isin,clean_price\nUS0000000001,100.5\n", encoding="utf-8")
    with testing.fakes(tmp_path):
        prices = fi_pricing.prices(DAY)
        curve = fi_curves.treasury_curve(DAY)
    assert prices.to_dict("list") == {"isin": ["US0000000001"], "clean_price": [100.5]}
    assert curve["tenor"].iloc[0] == "3M", "a source the app has no fixture for keeps the kit's"


def test_a_loader_without_sample_rows_says_where_to_write_them() -> None:
    with testing.fakes(), pytest.raises(SourceError, match=r"fixtures/fi_pricing/missing\.csv"):
        loaders.sample_rows(FI.source("fi_pricing"), "missing")


def test_app_query_reads_the_apps_fixture_named_for_its_sql_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loaders, "installed", lambda: FI)
    (tmp_path / "fi_pricing").mkdir()
    (tmp_path / "fi_pricing" / "watchlist.csv").write_text("isin,oas\nUS91282CJL69,0.0012\n", encoding="utf-8")
    with testing.fakes(tmp_path):
        watched = app_query("fi_pricing", "watchlist.sql", as_of=DAY)
    assert watched.to_dict("list") == {"isin": ["US91282CJL69"], "oas": [0.0012]}
    assert samples.served() == {"fi_pricing"}


def test_app_query_refuses_a_source_that_runs_no_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loaders, "installed", lambda: FI)
    with testing.fakes(), pytest.raises(SourceError, match="runs no SQL"):
        app_query("fi_curves", "curve.sql")


def test_query_outside_a_loader_says_where_it_belongs() -> None:
    with pytest.raises(SourceError, match="@loader"):
        query("prices.sql")


@pytest.mark.parametrize("frame", [pd.DataFrame({"trade_id": [1, 2]}), pd.DataFrame({"id": [1.5]}), pd.DataFrame({"account_id": pd.array([1, None], dtype="Int64")})])
def test_ids_that_come_back_as_numbers_are_refused(frame: pd.DataFrame) -> None:
    with pytest.raises(DataError, match="an id is text"):
        loaders.checked(frame, "fi_trades.trades")


def test_a_frame_comes_back_with_every_number_a_float() -> None:
    found = loaders.checked(pd.DataFrame({"trade_id": ["T1"], "quantity": [5_000_000], "flag": [True]}), "x")
    assert found.dtypes.astype(str).to_dict() == {"trade_id": "str", "quantity": "float64", "flag": "bool"}


def test_a_loader_that_returns_no_frame_is_an_error() -> None:
    with pytest.raises(DataError, match="returned list"):
        loaders.checked([1, 2], "x.y")
