"""The HTTP clients' own logic, against canned replies shaped like the services' documentation."""

import json
import secrets
from pathlib import Path

import httpx

from ctzd_api.clients import GitHubClient, JenkinsClient, ServiceAccountAuth


def _jenkins(transport: httpx.BaseTransport) -> JenkinsClient:
    return JenkinsClient("https://jenkins.contoso.com", user="u", api_token=secrets.token_hex(4), folder="ctzd-apps", transport=transport)


def _ref(number: int) -> dict[str, object]:
    return {"ref": f"refs/tags/ctzd/v{number}", "object": {"sha": f"sha{number}", "type": "tag"}}


def test_release_tags_reads_every_page_and_keeps_the_newest_by_number() -> None:
    # GitHub sorts refs by name, so v100 comes before v11, and pages them.
    names = sorted(range(1, 121), key=lambda number: f"v{number}")
    pages = [names[:100], names[100:]]

    def answer(request: httpx.Request) -> httpx.Response:
        if "/git/tags/" in request.url.path:
            sha = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"message": f"message {sha}"})
        page = int(request.url.params.get("page", "0"))
        links = {} if page == 1 else {"Link": f'<{request.url.copy_with(params={"page": 1})}>; rel="next"'}
        return httpx.Response(200, json=[_ref(number) for number in pages[page]], headers=links)

    tags = GitHubClient("https://github.contoso.com/api/v3", "ctzd-apps", "bot", transport=httpx.MockTransport(answer)).release_tags("fi-bond-prices")
    assert [name for name, _message in tags] == [f"ctzd/v{number}" for number in range(71, 121)]
    assert tags[-1][1] == "message sha120"


def test_a_builds_commit_is_the_apps_checkout_not_the_shared_librarys() -> None:
    build = {
        "number": 7,
        "building": False,
        "result": "SUCCESS",
        "url": "https://jenkins.contoso.com/job/ctzd-apps/job/fi-bond-prices/job/main/7/",
        "actions": [
            {"lastBuiltRevision": {"SHA1": "library"}, "remoteUrls": ["https://github.contoso.com/ctzd-platform/ctzd-jenkins.git"]},
            {},
            {"lastBuiltRevision": {"SHA1": "app"}, "remoteUrls": ["https://github.contoso.com/ctzd-apps/fi-bond-prices.git"]},
        ],
    }
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"builds": [build]}))
    found = _jenkins(transport).builds("fi-bond-prices")
    assert [item.commit for item in found] == ["app"]


def test_the_failed_stage_is_the_innermost_one() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/wfapi/describe"):
            stages = [{"name": "Checks", "status": "SUCCESS"}, {"name": "Ship", "status": "FAILED"}, {"name": "Go live", "status": "FAILED"}, {"name": "Smoke test", "status": "FAILED"}]
            return httpx.Response(200, json={"stages": stages})
        build = {"number": 8, "building": False, "result": "FAILURE", "url": "https://jenkins.contoso.com/b/8/", "actions": []}
        return httpx.Response(200, json={"builds": [build]})

    found = _jenkins(httpx.MockTransport(answer)).builds("fi-bond-prices")
    assert found[0].failed_stage == "Smoke test"


def test_the_service_account_token_is_read_again_on_every_request(tmp_path: Path) -> None:
    token = tmp_path / "token"
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, content=json.dumps({}))

    client = httpx.Client(transport=httpx.MockTransport(answer), auth=ServiceAccountAuth(token))
    token.write_text("first\n")
    client.get("https://kubernetes.default.svc/api")
    token.write_text("rotated\n")
    client.get("https://kubernetes.default.svc/api")
    assert seen == ["Bearer first", "Bearer rotated"]
