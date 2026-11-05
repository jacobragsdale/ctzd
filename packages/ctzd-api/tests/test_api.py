import datetime as dt
from typing import override

import httpx
import pytest
from fastapi.testclient import TestClient

from ctzd_api import fakes
from ctzd_api.app import create_app
from ctzd_api.gateways import AppState, JenkinsBuild
from ctzd_api.service import ROLLBACK_JOB, Services
from ctzd_cli import api_client, config, release
from ctzd_cli import api_models as m
from ctzd_cli.errors import CliError

APP_TOML = """
[app]
name = "bond-prices"
sbu = "fi"
ui = "streamlit"
owners = ["ann.lee@contoso.com"]

[[jobs]]
name = "refresh"
schedule = "0 6 * * *"
"""
ANN = {"Authorization": "Bearer ann-token"}
BOB = {"Authorization": "Bearer bob-token"}


def tag(number: int) -> tuple[str, str]:
    record = release.Release(release=number, commit=f"c{number}", image=f"i:{number}", created_at=dt.datetime(2026, 11, 4, tzinfo=dt.UTC), app_toml=APP_TOML)
    return (release.tag_name(number), release.message(record))


def build(number: int, *, commit: str, result: str | None, description: str | None = None, failed_stage: str | None = None) -> JenkinsBuild:
    return JenkinsBuild(
        number=number,
        commit=commit,
        building=False,
        result=result,
        url=f"https://jenkins.contoso.com/job/ctzd-apps/job/fi-bond-prices/job/main/{number}/",
        description=description,
        failed_stage=failed_stage,
        artifacts=(),
    )


@pytest.fixture
def world() -> Services:
    repo = fakes.FakeRepo(permissions={"ann": "admin", "bob": "read"}, files={"app.toml": APP_TOML}, tags=[tag(1), tag(2), tag(3)])
    return Services(
        github=fakes.FakeGitHub(tokens={"ann-token": "ann", "bob-token": "bob"}, members={"ann", "bob"}, repos={"fi-bond-prices": repo}),
        jenkins=fakes.FakeJenkins(),
        airflow=fakes.FakeAirflow(),
        kube=fakes.FakeKube(states={"fi-bond-prices": AppState(release=3, paused=False)}),
        secrets=fakes.FakeSecrets(values={"ctzd-source-fi-pricing": '{"kind":"oracle"}', "ctzd-shared-store-sas": "sv=2026&sig=x"}),
        platform=config.load(),
    )


@pytest.fixture
def client(world: Services) -> TestClient:
    return TestClient(create_app(world))


def test_requests_without_a_token_are_refused(client: TestClient) -> None:
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_a_member_creates_an_app_repo(client: TestClient, world: Services) -> None:
    response = client.post("/v1/apps", headers=BOB, json={"sbu": "fi", "name": "curve-viewer", "ui": "dash", "owners": ["bob@contoso.com"]})
    assert response.status_code == 201
    assert response.json()["clone_url"] == "https://github.contoso.com/ctzd-apps/fi-curve-viewer.git"
    assert world.github.permission("fi-curve-viewer", "bob") == "admin"


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"sbu": "fi", "name": "bond-prices", "ui": "streamlit", "owners": ["a@contoso.com"]}, 409),
        ({"sbu": "zz", "name": "new-app", "ui": "streamlit", "owners": ["a@contoso.com"]}, 400),
        ({"sbu": "fi", "name": "Bad_Name", "ui": "streamlit", "owners": ["a@contoso.com"]}, 400),
    ],
)
def test_create_refuses_duplicates_unknown_units_and_bad_names(client: TestClient, body: dict[str, object], status: int) -> None:
    assert client.post("/v1/apps", headers=ANN, json=body).status_code == status


def test_status_reports_release_jobs_and_last_build(client: TestClient, world: Services) -> None:
    jenkins = world.jenkins
    assert isinstance(jenkins, fakes.FakeJenkins)
    jenkins.by_repo["fi-bond-prices"] = [build(7, commit="abc", result="SUCCESS", description="release v3")]
    body = client.get("/v1/apps/fi/bond-prices", headers=BOB).json()
    assert body["release"] == 3
    assert body["dags"] == {"refresh": "https://airflow.ctzd.contoso.com/dags/fi__bond_prices__refresh"}
    assert body["last_build"]["state"] == "succeeded"


def test_a_failed_build_carries_its_stage_and_log(client: TestClient, world: Services) -> None:
    jenkins = world.jenkins
    assert isinstance(jenkins, fakes.FakeJenkins)
    jenkins.by_repo["fi-bond-prices"] = [build(8, commit="def", result="FAILURE", failed_stage="smoke test")]
    jenkins.consoles[8] = "line\nstException: KeyError 'price'"
    body = client.get("/v1/apps/fi/bond-prices/builds", params={"commit": "def"}, headers=ANN).json()
    assert body["state"] == "failed"
    assert body["stage"] == "smoke test"
    assert "KeyError" in body["log_tail"]


def test_an_unknown_commit_has_no_build_yet(client: TestClient) -> None:
    assert client.get("/v1/apps/fi/bond-prices/builds", params={"commit": "zzz"}, headers=ANN).json()["state"] == "not_found"


def test_rollback_defaults_to_the_release_before_the_live_one(client: TestClient, world: Services) -> None:
    body = client.post("/v1/apps/fi/bond-prices/rollback", headers=ANN, json={}).json()
    assert body["release"] == 2
    jenkins = world.jenkins
    assert isinstance(jenkins, fakes.FakeJenkins)
    assert jenkins.triggered == [(ROLLBACK_JOB, {"REPO": "fi-bond-prices", "RELEASE": "2"})]
    assert world.kube.state("fi-bond-prices").paused


def test_rollback_to_an_unknown_release_is_not_found(client: TestClient) -> None:
    assert client.post("/v1/apps/fi/bond-prices/rollback", headers=ANN, json={"release": 9}).status_code == 404


def test_read_access_cannot_roll_back_or_fetch_credentials(client: TestClient) -> None:
    assert client.post("/v1/apps/fi/bond-prices/rollback", headers=BOB, json={}).status_code == 403
    assert client.post("/v1/apps/fi/bond-prices/env", headers=BOB, json={"sources": ["fi_pricing"]}).status_code == 403


def test_env_returns_the_sources_and_the_shared_store_sas(client: TestClient) -> None:
    body = client.post("/v1/apps/fi/bond-prices/env", headers=ANN, json={"sources": ["fi_pricing"]}).json()
    assert body["variables"] == {"CTZD_SOURCE_FI_PRICING": '{"kind":"oracle"}', "CTZD_SHARED_STORE_SAS": "sv=2026&sig=x"}


def test_env_for_a_missing_source_says_who_adds_it(client: TestClient) -> None:
    response = client.post("/v1/apps/fi/bond-prices/env", headers=ANN, json={"sources": ["fi_trades"]})
    assert response.status_code == 404
    assert "platform team" in response.json()["detail"]


def test_running_a_job_returns_the_airflow_run_link(client: TestClient) -> None:
    body = client.post("/v1/apps/fi/bond-prices/jobs/refresh/runs", headers=ANN).json()
    assert body["url"] == "https://airflow.ctzd.contoso.com/dags/fi__bond_prices__refresh/runs/manual__1"
    assert client.post("/v1/apps/fi/bond-prices/jobs/other/runs", headers=ANN).status_code == 404


def test_only_admins_delete(client: TestClient, world: Services) -> None:
    repo = world.github
    assert isinstance(repo, fakes.FakeGitHub)
    repo.repos["fi-bond-prices"].permissions["carol"] = "write"
    repo.tokens["carol-token"] = "carol"
    assert client.delete("/v1/apps/fi/bond-prices", headers={"Authorization": "Bearer carol-token"}).status_code == 403
    assert client.delete("/v1/apps/fi/bond-prices", headers=ANN).status_code == 204
    assert repo.repos["fi-bond-prices"].archived


class InProcess(httpx.BaseTransport):
    """Carry the CLI's httpx requests to the in-process app through Starlette's test client."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        reply = self._client.request(request.method, str(request.url), headers=dict(request.headers), content=request.read())
        return httpx.Response(reply.status_code, headers=dict(reply.headers), content=reply.content)


def test_the_cli_client_speaks_the_same_protocol(client: TestClient) -> None:
    platform = api_client.PlatformClient("http://testserver", "ann-token", transport=InProcess(client))
    assert platform.status("fi", "bond-prices").release == 3
    assert platform.rollback("fi", "bond-prices", None).release == 2
    assert platform.env("fi", "bond-prices", ("fi_pricing",)).variables["CTZD_SOURCE_FI_PRICING"] == '{"kind":"oracle"}'
    assert platform.run_job("fi", "bond-prices", "refresh").dag_id == "fi__bond_prices__refresh"
    assert platform.build_for_commit("fi", "bond-prices", "nothing") == m.Build(number=None, state="not_found")
    with pytest.raises(CliError, match="no release v9"):
        platform.rollback("fi", "bond-prices", 9)
