"""HTTP implementations of the gateways. None of these has run against the real services yet; see docs/plan.md."""

from pathlib import Path

import httpx
from azure.core.credentials import TokenCredential
from pydantic import BaseModel, ConfigDict, TypeAdapter

from ctzd_api.gateways import AppState, JenkinsBuild, Permission

_TIMEOUT = 30.0
_RAW = "application/vnd.github.raw+json"
_JSON_OBJECT: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_PERMISSIONS: frozenset[Permission] = frozenset({"admin", "maintain", "write", "triage", "read", "none"})


class _Reply(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


def _check(response: httpx.Response) -> httpx.Response:
    response.raise_for_status()
    return response


class GitHubClient:
    """GitHub Enterprise through a bot token with admin rights on the ctzd-apps organization."""

    def __init__(self, api_url: str, org: str, bot_token: str) -> None:
        self._org = org
        self._api = api_url.rstrip("/")
        self._http = httpx.Client(base_url=self._api, timeout=_TIMEOUT, headers={"Authorization": f"Bearer {bot_token}", "Accept": "application/vnd.github+json"})

    def login_for(self, user_token: str) -> str | None:
        response = httpx.get(f"{self._api}/user", headers={"Authorization": f"Bearer {user_token}"}, timeout=_TIMEOUT)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            return None
        login = _JSON_OBJECT.validate_json(_check(response).content).get("login")
        return login if isinstance(login, str) else None

    def is_org_member(self, login: str) -> bool:
        return self._http.get(f"/orgs/{self._org}/members/{login}").status_code == httpx.codes.NO_CONTENT

    def permission(self, repo: str, login: str) -> Permission:
        response = self._http.get(f"/repos/{self._org}/{repo}/collaborators/{login}/permission")
        if response.status_code == httpx.codes.NOT_FOUND:
            return "none"
        body = _JSON_OBJECT.validate_json(_check(response).content)
        role = body.get("role_name", body.get("permission"))
        return next((permission for permission in _PERMISSIONS if permission == role), "none")

    def repo_exists(self, repo: str) -> bool:
        return self._http.get(f"/repos/{self._org}/{repo}").status_code == httpx.codes.OK

    def create_repo(self, repo: str, *, admin: str, team: str) -> None:
        _check(self._http.post(f"/orgs/{self._org}/repos", json={"name": repo, "visibility": "internal", "has_issues": False, "has_projects": False, "has_wiki": False}))
        _check(self._http.put(f"/repos/{self._org}/{repo}/collaborators/{admin}", json={"permission": "admin"}))
        _check(self._http.put(f"/orgs/{self._org}/teams/{team}/repos/{self._org}/{repo}", json={"permission": "push"}))
        ruleset = {
            "name": "ctzd main",
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        }
        _check(self._http.post(f"/repos/{self._org}/{repo}/rulesets", json=ruleset))

    def archive_repo(self, repo: str) -> None:
        _check(self._http.patch(f"/repos/{self._org}/{repo}", json={"archived": True}))

    def file(self, repo: str, path: str) -> str | None:
        response = self._http.get(f"/repos/{self._org}/{repo}/contents/{path}", headers={"Accept": _RAW})
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        return _check(response).text

    def release_tags(self, repo: str) -> list[tuple[str, str]]:
        response = self._http.get(f"/repos/{self._org}/{repo}/git/matching-refs/tags/ctzd/v")
        refs = TypeAdapter(list[_Ref]).validate_json(_check(response).content)
        tags: list[tuple[str, str]] = []
        for ref in refs[-50:]:
            if ref.object.type != "tag":
                continue
            tag = _Tag.model_validate_json(_check(self._http.get(f"/repos/{self._org}/{repo}/git/tags/{ref.object.sha}")).content)
            tags.append((ref.ref.removeprefix("refs/tags/"), tag.message))
        return tags


class _RefObject(_Reply):
    sha: str
    type: str


class _Ref(_Reply):
    ref: str
    object: _RefObject


class _Tag(_Reply):
    message: str


class _Revision(_Reply):
    SHA1: str


class _Action(_Reply):
    lastBuiltRevision: _Revision | None = None  # noqa: N815 - Jenkins' JSON field name.


class _Artifact(_Reply):
    relativePath: str  # noqa: N815 - Jenkins' JSON field name.


class _Build(_Reply):
    number: int
    building: bool
    result: str | None
    url: str
    description: str | None = None
    artifacts: tuple[_Artifact, ...] = ()
    actions: tuple[_Action, ...] = ()


class _Builds(_Reply):
    builds: tuple[_Build, ...] = ()


class _Stage(_Reply):
    name: str
    status: str


class _Run(_Reply):
    stages: tuple[_Stage, ...] = ()


class JenkinsClient:
    def __init__(self, url: str, *, user: str, api_token: str, folder: str) -> None:
        self._url = url.rstrip("/")
        self._folder = folder
        self._http = httpx.Client(timeout=_TIMEOUT, auth=(user, api_token))

    def builds(self, repo: str) -> list[JenkinsBuild]:
        tree = "builds[number,building,result,url,description,artifacts[relativePath],actions[lastBuiltRevision[SHA1]]]{0,30}"
        response = self._http.get(f"{self._url}/job/{self._folder}/job/{repo}/job/main/api/json", params={"tree": tree})
        if response.status_code == httpx.codes.NOT_FOUND:
            return []
        found = []
        for build in _Builds.model_validate_json(_check(response).content).builds:
            commit = next((action.lastBuiltRevision.SHA1 for action in build.actions if action.lastBuiltRevision is not None), None)
            failed = None if build.building or build.result in {None, "SUCCESS"} else self._failed_stage(build.url)
            found.append(
                JenkinsBuild(
                    number=build.number,
                    commit=commit,
                    building=build.building,
                    result=build.result,
                    url=build.url,
                    description=build.description,
                    failed_stage=failed,
                    artifacts=tuple(artifact.relativePath for artifact in build.artifacts),
                )
            )
        return found

    def _failed_stage(self, build_url: str) -> str | None:
        response = self._http.get(f"{build_url}wfapi/describe")
        if not response.is_success:
            return None
        return next((stage.name for stage in _Run.model_validate_json(response.content).stages if stage.status == "FAILED"), None)

    def console_tail(self, build: JenkinsBuild, lines: int) -> str:
        return "\n".join(_check(self._http.get(f"{build.url}consoleText")).text.splitlines()[-lines:])

    def artifact(self, build: JenkinsBuild, name: str) -> bytes:
        return _check(self._http.get(f"{build.url}artifact/{name}")).content

    def trigger(self, job: str, parameters: dict[str, str]) -> None:
        _check(self._http.post(f"{self._url}/job/{job}/buildWithParameters", data=parameters))


class _DagRun(_Reply):
    dag_run_id: str


class _DagRuns(_Reply):
    dag_runs: tuple[_DagRun, ...] = ()


class _TaskInstance(_Reply):
    task_id: str
    try_number: int


class _TaskInstances(_Reply):
    task_instances: tuple[_TaskInstance, ...] = ()


class _AirflowToken(_Reply):
    access_token: str


class AirflowClient:
    """Airflow 3's public REST API. With the simple auth manager in all-admins mode no credentials are set."""

    def __init__(self, url: str, *, username: str | None, password: str | None) -> None:
        self._url = url.rstrip("/")
        self._http = httpx.Client(base_url=self._url, timeout=_TIMEOUT)
        self._credentials = None if username is None or password is None else {"username": username, "password": password}

    def _headers(self) -> dict[str, str]:
        if self._credentials is None:
            return {}
        token = _AirflowToken.model_validate_json(_check(self._http.post("/auth/token", json=self._credentials)).content)
        return {"Authorization": f"Bearer {token.access_token}"}

    def trigger(self, dag_id: str) -> str:
        response = self._http.post(f"/api/v2/dags/{dag_id}/dagRuns", json={"logical_date": None}, headers=self._headers())
        return _DagRun.model_validate_json(_check(response).content).dag_run_id

    def latest_task_log(self, dag_id: str, tail: int) -> str:
        headers = self._headers()
        runs = _DagRuns.model_validate_json(_check(self._http.get(f"/api/v2/dags/{dag_id}/dagRuns", params={"order_by": "-start_date", "limit": 1}, headers=headers)).content)
        if not runs.dag_runs:
            return ""
        run = runs.dag_runs[0].dag_run_id
        tasks = _TaskInstances.model_validate_json(_check(self._http.get(f"/api/v2/dags/{dag_id}/dagRuns/{run}/taskInstances", headers=headers)).content)
        if not tasks.task_instances:
            return ""
        task = tasks.task_instances[0]
        response = self._http.get(
            f"/api/v2/dags/{dag_id}/dagRuns/{run}/taskInstances/{task.task_id}/logs/{task.try_number}", params={"full_content": "true"}, headers=headers | {"Accept": "text/plain"}
        )
        return "\n".join(_check(response).text.splitlines()[-tail:])


_SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class _ConfigMap(_Reply):
    data: dict[str, str] = {}


class _PodMeta(_Reply):
    name: str


class _Pod(_Reply):
    metadata: _PodMeta


class _Pods(_Reply):
    items: tuple[_Pod, ...] = ()


class KubeClient:
    """The Kubernetes API from inside the cluster, with the pod's service account."""

    def __init__(self, namespace: str, *, api_url: str = "https://kubernetes.default.svc") -> None:
        self._ns = namespace
        token = (_SERVICE_ACCOUNT / "token").read_text(encoding="utf-8").strip()
        self._http = httpx.Client(base_url=api_url, timeout=_TIMEOUT, verify=str(_SERVICE_ACCOUNT / "ca.crt"), headers={"Authorization": f"Bearer {token}"})

    def _state_path(self, fullname: str) -> str:
        return f"/api/v1/namespaces/{self._ns}/configmaps/ctzd-state-{fullname}"

    def state(self, fullname: str) -> AppState:
        response = self._http.get(self._state_path(fullname))
        if response.status_code == httpx.codes.NOT_FOUND:
            return AppState(release=None, paused=False)
        data = _ConfigMap.model_validate_json(_check(response).content).data
        release = data.get("release", "")
        return AppState(release=int(release) if release.isdigit() else None, paused=data.get("paused") == "true")

    def set_paused(self, fullname: str, *, paused: bool) -> None:
        patch = {"data": {"paused": "true" if paused else "false"}}
        response = self._http.patch(self._state_path(fullname), json=patch, headers={"Content-Type": "application/merge-patch+json"})
        if response.status_code == httpx.codes.NOT_FOUND:
            body = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": f"ctzd-state-{fullname}"}} | patch
            _check(self._http.post(f"/api/v1/namespaces/{self._ns}/configmaps", json=body))
            return
        _check(response)

    def dashboard_log(self, fullname: str, tail: int) -> str:
        pods = _Pods.model_validate_json(_check(self._http.get(f"/api/v1/namespaces/{self._ns}/pods", params={"labelSelector": f"app.kubernetes.io/instance={fullname}"})).content)
        if not pods.items:
            return ""
        name = pods.items[0].metadata.name
        return _check(self._http.get(f"/api/v1/namespaces/{self._ns}/pods/{name}/log", params={"tailLines": tail})).text


class _Secret(_Reply):
    value: str


class KeyVaultClient:
    def __init__(self, vault_url: str, credential: TokenCredential) -> None:
        self._url = vault_url.rstrip("/")
        self._credential = credential
        self._http = httpx.Client(timeout=_TIMEOUT)

    def get(self, name: str) -> str | None:
        token = self._credential.get_token("https://vault.azure.net/.default").token
        response = self._http.get(f"{self._url}/secrets/{name}", params={"api-version": "7.4"}, headers={"Authorization": f"Bearer {token}"})
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        return _Secret.model_validate_json(_check(response).content).value
