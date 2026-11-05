import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ctzd_cli import api_models as m
from ctzd_cli import checks, commands, gitops
from ctzd_cli.errors import CliError

from .conftest import make_app

GIT = gitops.find_git()


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run([GIT, "-c", "user.name=t", "-c", "user.email=t@contoso.com", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@dataclass
class FakePlatform:
    builds: list[m.Build]
    resumed: int = 0
    asked: list[str] = field(default_factory=list)
    paused: bool = False
    job_states: list[m.JobState] = field(default_factory=lambda: ["queued"])

    def me(self) -> m.Me:
        return m.Me(login="ann")

    def create_app(self, request: m.CreateAppRequest) -> m.CreatedApp:
        return m.CreatedApp(repo=f"ctzd-apps/{request.sbu}-{request.name}", clone_url="unused", url="unused")

    def status(self, sbu: str, name: str) -> m.AppStatus:
        return m.AppStatus(sbu=sbu, name=name, url=f"https://{sbu}-{name}.ctzd.contoso.com", release=4, paused=self.paused, dags={}, last_build=None)

    def resume(self, sbu: str, name: str) -> None:
        del sbu, name
        self.resumed += 1

    def build_for_commit(self, sbu: str, name: str, commit: str) -> m.Build:
        del sbu, name
        self.asked.append(commit)
        return self.builds.pop(0) if len(self.builds) > 1 else self.builds[0]

    def screenshot(self, sbu: str, name: str, number: int) -> bytes:
        del sbu, name, number
        return b"\x89PNG fake"

    def rollback(self, sbu: str, name: str, release: int | None) -> m.RollbackAccepted:
        raise NotImplementedError

    def run_job(self, sbu: str, name: str, job: str) -> m.JobRunCreated:
        return m.JobRunCreated(dag_id=f"{sbu}__{name.replace('-', '_')}__{job}", run_id="manual__1", url="https://airflow.ctzd.contoso.com/dags/x/runs/manual__1")

    def job_run(self, sbu: str, name: str, job: str, run_id: str) -> m.JobRunState:
        state = self.job_states.pop(0) if len(self.job_states) > 1 else self.job_states[0]
        return m.JobRunState(dag_id=f"{sbu}__{name.replace('-', '_')}__{job}", run_id=run_id, state=state, url="https://airflow.ctzd.contoso.com/dags/x/runs/manual__1")

    def sources(self) -> m.SourceList:
        return m.SourceList(sources=("fi_pricing",))

    def logs(self, sbu: str, name: str, *, job: str | None, tail: int) -> m.LogText:
        raise NotImplementedError

    def env(self, sbu: str, name: str, sources: tuple[str, ...]) -> m.EnvVars:
        raise NotImplementedError

    def delete(self, sbu: str, name: str) -> None:
        raise NotImplementedError


def passing(_target: checks.Target, *, mode: checks.Mode, fast: bool) -> list[checks.Step]:
    del mode, fast
    return [checks.Step("tests", "passed")]


def failing(_target: checks.Target, *, mode: checks.Mode, fast: bool) -> list[checks.Step]:
    del mode, fast
    return [checks.Step("types", "failed", "src/app/logic/summary.py:3: error")]


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path]:
    app, remote = tmp_path / "app", tmp_path / "remote.git"
    make_app(app)
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch=main", str(remote))
    git(app, "init", "--quiet", "--initial-branch=main")
    git(app, "remote", "add", "origin", str(remote))
    git(app, "add", "--all")
    git(app, "commit", "--quiet", "-m", "Create")
    git(app, "push", "--quiet", "origin", "main")
    return app, remote


def context(app: Path, platform: FakePlatform, runner: commands.CheckRunner = passing) -> commands.Context:
    return commands.Context(cwd=app, platform_factory=lambda: platform, token=lambda: "token", run_checks=runner, sleep=lambda _s: None)


def test_deploy_pushes_and_reports_the_live_release(repo: tuple[Path, Path]) -> None:
    app, remote = repo
    (app / "src" / "app" / "logic" / "summary.py").write_text('"""changed"""\n')
    platform = FakePlatform([m.Build(number=8, state="running"), m.Build(number=8, state="succeeded", release=4, has_screenshot=True)])
    outcome = commands.deploy(context(app, platform), message=None, detach=False, timeout=60)
    assert outcome.ok
    assert outcome.data["release"] == 4
    assert outcome.data["url"] == "https://fi-bond-prices.ctzd.contoso.com"
    assert (app / ".ctzd" / "screenshots" / "v4.png").read_bytes() == b"\x89PNG fake"
    assert git(remote, "rev-parse", "main") == git(app, "rev-parse", "HEAD")
    assert platform.resumed == 1
    assert "summary.py" in git(app, "log", "-1", "--format=%s")


def test_a_failed_build_says_production_is_unchanged(repo: tuple[Path, Path]) -> None:
    app, _remote = repo
    platform = FakePlatform([m.Build(number=9, state="failed", stage="smoke test", log_tail="stException: KeyError 'price'")])
    outcome = commands.deploy(context(app, platform), message="Try", detach=False, timeout=60)
    assert not outcome.ok
    assert "smoke test" in outcome.summary
    assert "previous release" in outcome.summary


def test_failing_checks_stop_the_deploy_before_anything_is_pushed(repo: tuple[Path, Path]) -> None:
    app, remote = repo
    before = git(remote, "rev-parse", "main")
    (app / "README.md").write_text("changed\n")
    outcome = commands.deploy(context(app, FakePlatform([m.Build(number=1, state="succeeded")]), failing), message=None, detach=False, timeout=60)
    assert not outcome.ok
    assert outcome.summary.startswith("Not deployed.")
    assert git(remote, "rev-parse", "main") == before


def test_a_conflicting_change_from_someone_else_is_named(repo: tuple[Path, Path], tmp_path: Path) -> None:
    app, remote = repo
    other = tmp_path / "other"
    git(tmp_path, "clone", "--quiet", str(remote), str(other))
    (other / "README.md").write_text("theirs\n")
    git(other, "commit", "--quiet", "-am", "Theirs")
    git(other, "push", "--quiet", "origin", "main")
    (app / "README.md").write_text("mine\n")
    with pytest.raises(CliError) as caught:
        commands.deploy(context(app, FakePlatform([m.Build(number=1, state="succeeded")])), message=None, detach=False, timeout=60)
    assert caught.value.hint is not None
    assert "README.md" in caught.value.hint


def test_waiting_gives_up_at_the_timeout_and_says_how_to_continue(repo: tuple[Path, Path]) -> None:
    app, _remote = repo
    clock = iter(range(0, 10_000, 100))
    ctx = commands.Context(
        cwd=app, platform_factory=lambda: FakePlatform([m.Build(number=2, state="running")]), token=lambda: "token", run_checks=passing, sleep=lambda _s: None, clock=lambda: float(next(clock))
    )
    outcome = commands.deploy(ctx, message=None, detach=False, timeout=300)
    assert outcome.ok
    assert "still running" in outcome.summary
    assert outcome.next_steps == ["Run `ctzd status --wait` to keep waiting."]


def test_deploying_without_changes_after_a_rollback_makes_a_new_build(repo: tuple[Path, Path]) -> None:
    app, remote = repo
    before = git(remote, "rev-parse", "main")
    platform = FakePlatform([m.Build(number=10, state="succeeded", release=5)], paused=True)
    outcome = commands.deploy(context(app, platform), message=None, detach=False, timeout=60)
    assert outcome.ok
    assert git(remote, "rev-parse", "main") != before
    assert git(app, "log", "-1", "--format=%s") == "Resume auto-deploy after a rollback"


def test_deploying_without_changes_when_not_paused_pushes_nothing_new(repo: tuple[Path, Path]) -> None:
    app, remote = repo
    before = git(remote, "rev-parse", "main")
    commands.deploy(context(app, FakePlatform([m.Build(number=10, state="succeeded", release=4)])), message=None, detach=False, timeout=60)
    assert git(remote, "rev-parse", "main") == before


def test_run_wait_follows_the_airflow_run_to_success(repo: tuple[Path, Path]) -> None:
    app, _remote = repo
    (app / "app.toml").write_text((app / "app.toml").read_text() + '\n[[jobs]]\nname = "refresh"\nschedule = "0 6 * * *"\n')
    platform = FakePlatform([m.Build(number=1, state="succeeded")], job_states=["queued", "running", "success"])
    outcome = commands.run_job(context(app, platform), job="refresh", local=False, wait=True, timeout=600)
    assert outcome.ok
    assert outcome.data["state"] == "success"


def test_run_wait_reports_a_failed_run(repo: tuple[Path, Path]) -> None:
    app, _remote = repo
    (app / "app.toml").write_text((app / "app.toml").read_text() + '\n[[jobs]]\nname = "refresh"\nschedule = "0 6 * * *"\n')
    outcome = commands.run_job(context(app, FakePlatform([m.Build(number=1, state="succeeded")], job_states=["failed"])), job="refresh", local=False, wait=True, timeout=600)
    assert not outcome.ok
    assert "ctzd logs --job refresh" in outcome.next_steps[0]
