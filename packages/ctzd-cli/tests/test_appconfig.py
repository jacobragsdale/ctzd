from pathlib import Path

import pytest

from ctzd_cli import appconfig
from ctzd_cli.appconfig import Job
from ctzd_cli.errors import CliError

from .conftest import app_config, make_app

VALID = """
[app]
name = "bond-prices"
sbu = "fi"
ui = "streamlit"
owners = ["ann.lee@contoso.com"]

[[jobs]]
name = "refresh"
schedule = "0 6 * * 1-5"
"""


def test_valid_app_toml_parses_with_defaults() -> None:
    config = appconfig.parse(VALID)
    assert config.app.size == "s"
    assert config.jobs[0].retries == 1
    assert config.sources.use == ()


@pytest.mark.parametrize("name", ["ab", "Bond", "bond_prices", "-bond", "bond-", "b" * 31])
def test_bad_app_names_are_rejected(name: str) -> None:
    with pytest.raises(CliError, match="app name"):
        appconfig.parse(VALID.replace('"bond-prices"', f'"{name}"'))


@pytest.mark.parametrize("schedule", ["0 6 * *", "every day", "0 6 * * 1-5 2026"])
def test_schedules_must_be_five_field_cron(schedule: str) -> None:
    with pytest.raises(ValueError, match="five-field cron"):
        Job(name="refresh", schedule=schedule)


def test_duplicate_job_names_are_rejected() -> None:
    with pytest.raises(CliError, match="unique"):
        appconfig.parse(VALID + '\n[[jobs]]\nname = "refresh"\nschedule = "0 7 * * *"\n')


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(CliError, match="replicas"):
        appconfig.parse(VALID.replace('ui = "streamlit"', 'ui = "streamlit"\nreplicas = 3'))


def test_render_round_trips() -> None:
    config = app_config(jobs=(Job(name="refresh", schedule="0 6 * * *"),))
    assert appconfig.parse(appconfig.render(config)) == config


def test_add_job_keeps_the_files_other_content(tmp_path: Path) -> None:
    make_app(tmp_path)
    path = tmp_path / "app.toml"
    path.write_text(path.read_text() + "# keep me\n")
    updated = appconfig.add_job(tmp_path, Job(name="nightly", schedule="0 2 * * *"))
    assert [job.name for job in updated.jobs] == ["nightly"]
    assert "# keep me" in path.read_text()


def test_add_job_refuses_a_duplicate(tmp_path: Path) -> None:
    make_app(tmp_path, app_config(jobs=(Job(name="nightly", schedule="0 2 * * *"),)))
    with pytest.raises(CliError, match="already has"):
        appconfig.add_job(tmp_path, Job(name="nightly", schedule="0 3 * * *"))


def test_find_root_walks_up_to_app_toml(tmp_path: Path) -> None:
    make_app(tmp_path)
    assert appconfig.find_root(tmp_path / "src" / "app" / "data") == tmp_path


def test_find_root_outside_an_app_says_so(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="not inside a ctzd app"):
        appconfig.find_root(tmp_path)
