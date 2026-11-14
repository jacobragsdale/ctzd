"""`ctzd_kit.testing` and the settings it gives in place of the environment's."""

import os
from pathlib import Path

import pytest

from ctzd_common import samples
from ctzd_kit import testing
from ctzd_kit.settings import session_name, settings


@pytest.fixture(autouse=True)
def desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop with nothing faked, no source's secret, and no sample rows served yet."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    samples.reset()


def test_outside_an_app_it_is_the_desktop_with_nothing_faked() -> None:
    found = settings()
    assert (found.env, found.fake, found.fixtures, found.store_url, found.owners) == ("local", False, None, None, ())


def test_given_settings_stack_and_unwind() -> None:
    with testing.given({"CTZD_ENV": "dev", "CTZD_OWNERS": "ann.lee@contoso.com,,bo.chan@contoso.com"}):
        with testing.given({"CTZD_APP": "bond-prices", "CTZD_SBU": "fi"}):
            inner = settings()
        outer = settings()
    assert (inner.env, inner.app, inner.owners) == ("dev", "bond-prices", ("ann.lee@contoso.com", "bo.chan@contoso.com"))
    assert (outer.env, outer.app) == ("dev", "")
    assert settings().env == "local"


def test_an_unknown_environment_is_the_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_ENV", "staging")
    assert settings().env == "local"
    monkeypatch.setenv("CTZD_ENV", "prod")
    assert settings().env == "prod"


def test_fakes_point_at_the_apps_fixtures(tmp_path: Path) -> None:
    with testing.fakes(tmp_path):
        assert settings().fake
        assert settings().fixtures == tmp_path
    with testing.fakes():
        assert settings().fixtures is None


def test_a_database_session_names_the_app() -> None:
    assert session_name() == "ctzd"
    with testing.given({"CTZD_APP": "bond-prices", "CTZD_SBU": "fi"}):
        assert session_name() == "ctzd:fi-bond-prices"
