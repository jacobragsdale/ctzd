import datetime as dt

from ctzd_cli import config, release, render
from ctzd_cli.appconfig import Job

from .conftest import app_config


def _record(number: int) -> tuple[str, str]:
    record = release.Release(release=number, commit=f"c{number}", image=f"img:{number}", created_at=dt.datetime(2026, 11, 4, tzinfo=dt.UTC), app_toml="")
    return (release.tag_name(number), release.message(record))


def test_releases_are_listed_newest_first_and_other_tags_ignored() -> None:
    found = release.parse_tags([_record(1), ("v2.0", "not a release"), _record(3), ("ctzd/v9", "{broken json")])
    assert [item.release for item in found] == [3, 1]


def test_next_release_number_follows_the_highest() -> None:
    assert release.next_number(release.parse_tags([_record(1), _record(4)])) == 5
    assert release.next_number([]) == 1


def test_react_values_run_the_app_server_with_the_api_s_readiness() -> None:
    values = render.helm_values(config.load(), app_config(), image="acr/fi-bond-prices:abc", release=7)
    assert values.enabled
    assert values.host == "fi-bond-prices.ctzd.contoso.com"
    assert values.readiness_path == "/health/ready"
    assert values.command[:3] == ("uvicorn", "--factory", "ctzd.api:asgi")
    assert values.model_dump(by_alias=True)["readinessPath"] == "/health/ready"


def test_streamlit_values_run_streamlit_with_its_health_endpoint() -> None:
    values = render.helm_values(config.load(), app_config(ui="streamlit"), image="i", release=None)
    assert values.command[:2] == ("streamlit", "run")
    assert values.readiness_path == "/_stcore/health"


def test_dash_values_serve_with_gunicorn() -> None:
    values = render.helm_values(config.load(), app_config(ui="dash"), image="i", release=None)
    assert values.command[0] == "gunicorn"
    assert values.readiness_path == "/healthz"


def test_jobs_only_app_has_no_dashboard() -> None:
    assert not render.helm_values(config.load(), app_config(ui="none"), image="i", release=None).enabled


def test_airflow_spec_names_each_dag_and_runs_its_module() -> None:
    app = app_config(jobs=(Job(name="refresh", schedule="0 6 * * *", size="l"),))
    spec = render.airflow_spec(config.load(), app, image="acr/fi-bond-prices:abc", release=3)
    (job,) = spec.jobs
    assert job.dag_id == "fi__bond_prices__refresh"
    assert job.command == ("python", "-c", "import sys; from app.jobs.refresh import main; sys.exit(main())")
    assert job.resources.limits.memory == "8Gi"
    assert spec.owners == ("ann.lee@contoso.com",)


def test_pods_get_one_secret_per_listed_source_and_no_others() -> None:
    app = app_config(sources=("fi_pricing", "fi_api"), jobs=(Job(name="refresh", schedule="0 6 * * *"),))
    values = render.helm_values(config.load(), app, image="i", release=1)
    spec = render.airflow_spec(config.load(), app, image="i", release=1)
    assert values.model_dump(by_alias=True)["sourceSecrets"] == ("ctzd-source-fi-pricing", "ctzd-source-fi-api")
    assert spec.source_secrets == values.source_secrets
    assert spec.version == 2
    assert render.helm_values(config.load(), app_config(), image="i", release=1).source_secrets == ()


def test_rendered_values_read_back_as_written() -> None:
    values = render.helm_values(config.load(), app_config(sources=("fi_pricing",)), image="i", release=1)
    written = values.model_dump_json(by_alias=True)
    assert '"readinessPath"' in written
    assert render.HelmValues.model_validate_json(written) == values


def test_each_job_spec_carries_its_time_zone() -> None:
    app = app_config(jobs=(Job(name="summary", schedule="0 7 * * 1-5", timezone="Europe/London"),))
    (job,) = render.airflow_spec(config.load(), app, image="i", release=1).jobs
    assert job.timezone == "Europe/London"
