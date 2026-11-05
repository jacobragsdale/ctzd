"""In-memory gateways for tests and for running the API locally without GitHub, Jenkins, Airflow, or a cluster."""

from dataclasses import dataclass, field

from ctzd_api.gateways import AppState, JenkinsBuild, Permission


@dataclass
class FakeRepo:
    permissions: dict[str, Permission] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    tags: list[tuple[str, str]] = field(default_factory=list)
    team: str | None = None
    archived: bool = False


@dataclass
class FakeGitHub:
    tokens: dict[str, str] = field(default_factory=dict)
    members: set[str] = field(default_factory=set)
    repos: dict[str, FakeRepo] = field(default_factory=dict)

    def login_for(self, user_token: str) -> str | None:
        return self.tokens.get(user_token)

    def is_org_member(self, login: str) -> bool:
        return login in self.members

    def permission(self, repo: str, login: str) -> Permission:
        return self.repos[repo].permissions.get(login, "none")

    def repo_exists(self, repo: str) -> bool:
        return repo in self.repos

    def create_repo(self, repo: str, *, admin: str, team: str) -> None:
        self.repos[repo] = FakeRepo(permissions={admin: "admin"}, team=team)

    def archive_repo(self, repo: str) -> None:
        self.repos[repo].archived = True

    def file(self, repo: str, path: str) -> str | None:
        return self.repos[repo].files.get(path)

    def release_tags(self, repo: str) -> list[tuple[str, str]]:
        return list(self.repos[repo].tags)


@dataclass
class FakeJenkins:
    by_repo: dict[str, list[JenkinsBuild]] = field(default_factory=dict)
    consoles: dict[int, str] = field(default_factory=dict)
    triggered: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def builds(self, repo: str) -> list[JenkinsBuild]:
        return list(self.by_repo.get(repo, []))

    def console_tail(self, build: JenkinsBuild, lines: int) -> str:
        return "\n".join(self.consoles.get(build.number, "").splitlines()[-lines:])

    def artifact(self, build: JenkinsBuild, name: str) -> bytes:
        return f"{name} of build {build.number}".encode()

    def trigger(self, job: str, parameters: dict[str, str]) -> None:
        self.triggered.append((job, parameters))


@dataclass
class FakeAirflow:
    runs: list[str] = field(default_factory=list)
    logs: dict[str, str] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)

    def trigger(self, dag_id: str) -> str:
        self.runs.append(dag_id)
        return f"manual__{len(self.runs)}"

    def run_state(self, dag_id: str, run_id: str) -> str:
        del dag_id
        return self.states.get(run_id, "queued")

    def latest_task_log(self, dag_id: str, tail: int) -> str:
        return "\n".join(self.logs.get(dag_id, "").splitlines()[-tail:])


@dataclass
class FakeKube:
    states: dict[str, AppState] = field(default_factory=dict)
    logs: dict[str, str] = field(default_factory=dict)

    def state(self, fullname: str) -> AppState:
        return self.states.get(fullname, AppState(release=None, paused=False))

    def set_paused(self, fullname: str, *, paused: bool) -> None:
        self.states[fullname] = AppState(release=self.state(fullname).release, paused=paused)

    def dashboard_log(self, fullname: str, tail: int) -> str:
        return "\n".join(self.logs.get(fullname, "").splitlines()[-tail:])


@dataclass
class FakeSecrets:
    values: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def names(self, prefix: str) -> list[str]:
        return sorted(name for name in self.values if name.startswith(prefix))
