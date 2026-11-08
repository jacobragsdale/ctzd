import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from ctzd_cli import api_models as m
from ctzd_cli import checks, commands, gitops, releases
from ctzd_cli.errors import CliError, NotFoundError, UsageError

from .conftest import FakePlatform, make_app

LATER = releases.seed().release + 1  # a platform release published after the bundled one

GIT = gitops.find_git()


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run([GIT, "-c", "user.name=t", "-c", "user.email=t@contoso.com", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


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
    platform = FakePlatform([m.Build(number=8, state="running"), m.Build(number=8, state="succeeded", release=4)])
    outcome = commands.deploy(context(app, platform), message=None, detach=False, timeout=60)
    assert outcome.ok
    assert outcome.data["release"] == 4
    assert outcome.data["url"] == "https://fi-bond-prices.ctzd.contoso.com"
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


class RecordingPlatform(FakePlatform):
    """Records where `main` pointed on the remote when the pause was cleared."""

    def __init__(self, remote: Path, builds: list[m.Build], *, paused: bool = False) -> None:
        super().__init__(builds, paused=paused)
        self.remote = remote
        self.main_at_resume: str | None = None

    @override
    def resume(self, sbu: str, name: str) -> None:
        super().resume(sbu, name)
        self.main_at_resume = git(self.remote, "rev-parse", "main")


def test_the_pause_is_cleared_before_the_push(repo: tuple[Path, Path]) -> None:
    app, remote = repo
    before = git(remote, "rev-parse", "main")
    (app / "README.md").write_text("changed\n")
    platform = RecordingPlatform(remote, [m.Build(number=8, state="succeeded", release=5)], paused=True)
    commands.deploy(context(app, platform), message=None, detach=False, timeout=60)
    assert platform.main_at_resume == before
    assert git(remote, "rev-parse", "main") != before


def test_a_build_that_deployed_nothing_is_not_reported_as_live(repo: tuple[Path, Path]) -> None:
    app, _remote = repo
    (app / "README.md").write_text("changed\n")
    outcome = commands.deploy(context(app, FakePlatform([m.Build(number=8, state="succeeded", release=None)])), message=None, detach=False, timeout=60)
    assert not outcome.ok
    assert "nothing was deployed" in outcome.summary
    assert "vNone" not in outcome.summary


def upgrading(target: checks.Target, *, mode: checks.Mode, fast: bool) -> list[checks.Step]:
    """A gate run in which the platform-files step moved the app to the release after the bundled one."""
    del mode, fast
    (target.root / ".ctzd" / "version").write_text(f"{LATER}\n")
    (target.root / "ruff.toml").write_text("line-length = 120\n")
    return [checks.Step("platform files", "fixed", "Upgraded", paths=(".ctzd/version", "ruff.toml")), checks.Step("tests", "passed")]


def test_platform_upgrades_get_their_own_commit_before_the_change(repo: tuple[Path, Path]) -> None:
    app, remote = repo
    (app / "src" / "app" / "logic" / "summary.py").write_text('"""changed"""\n')
    platform = FakePlatform([m.Build(number=8, state="succeeded", release=5)])
    outcome = commands.deploy(context(app, platform, upgrading), message=None, detach=False, timeout=60)
    assert outcome.ok
    log = git(app, "log", "-3", "--format=%s").splitlines()
    assert log[0] == "Update src/app/logic/summary.py"
    assert log[1] == f"Upgrade platform files to platform release {LATER}"
    assert git(app, "show", "--name-only", "--format=", "HEAD~1").splitlines() == [".ctzd/version", "ruff.toml"]
    assert git(remote, "rev-parse", "main") == git(app, "rev-parse", "HEAD")


def test_a_platform_upgrade_alone_is_enough_to_end_a_pause(repo: tuple[Path, Path]) -> None:
    app, _remote = repo
    platform = FakePlatform([m.Build(number=8, state="succeeded", release=5)], paused=True)
    commands.deploy(context(app, platform, upgrading), message=None, detach=False, timeout=60)
    assert git(app, "log", "-1", "--format=%s") == f"Upgrade platform files to platform release {LATER}"


def test_an_upgrade_made_by_an_earlier_check_still_gets_its_own_commit(repo: tuple[Path, Path]) -> None:
    app, _remote = repo
    # What `ctzd check` leaves behind when it moved the app to a release with a new ruff.toml.
    (app / ".ctzd" / "version").write_text(f"{LATER}\n")
    (app / "ruff.toml").write_text((app / "ruff.toml").read_text() + f"# release {LATER}\n")
    (app / "README.md").write_text("changed\n")
    commands.deploy(context(app, FakePlatform([m.Build(number=8, state="succeeded", release=5)])), message=None, detach=False, timeout=60)
    assert git(app, "log", "-2", "--format=%s").splitlines() == ["Update README.md", f"Upgrade platform files to platform release {LATER}"]


def test_pyproject_is_the_platforms_only_while_the_person_changed_nothing_else_in_it(repo: tuple[Path, Path]) -> None:
    app, _remote = repo
    path = app / "pyproject.toml"
    path.write_text(path.read_text().replace('"ruff==0.17.3"', '"ruff==0.17.4"'))
    ctx = context(app, FakePlatform([m.Build(number=8, state="succeeded", release=5)]))
    assert commands.platform_changes(ctx, app, gitops.open_repo(app, token=None, identity=None)) == ["pyproject.toml"]
    path.write_text(path.read_text().replace('"ctzd-fi>=0.1"]', '"ctzd-fi>=0.1", "plotly>=6"]'))
    assert commands.platform_changes(ctx, app, gitops.open_repo(app, token=None, identity=None)) == []


def test_each_release_tag_gets_the_next_number_and_all_are_listed(repo: tuple[Path, Path]) -> None:
    app, _remote = repo
    head = git(app, "rev-parse", "HEAD")
    ctx = context(app, FakePlatform([m.Build(number=1, state="succeeded")]))
    moment = datetime(2026, 11, 6, 9, tzinfo=UTC)
    first = commands.ci_release(ctx, image="reg/app:1", commit=head, now=lambda: moment)
    second = commands.ci_release(ctx, image="reg/app:2", commit=head, now=lambda: moment)
    assert (first.data["tag"], second.data["tag"]) == ("ctzd/v1", "ctzd/v2")
    lines = commands.list_releases(ctx).summary.splitlines()
    assert [line.split()[0] for line in lines] == ["v2", "v1"]


def test_deploying_without_changes_after_a_failed_build_builds_again(repo: tuple[Path, Path]) -> None:
    app, remote = repo
    before = git(remote, "rev-parse", "main")
    platform = FakePlatform([m.Build(number=9, state="failed", stage="Image"), m.Build(number=10, state="succeeded", release=6)])
    outcome = commands.deploy(context(app, platform), message=None, detach=False, timeout=60)
    assert outcome.ok
    assert outcome.data["release"] == 6
    assert git(remote, "rev-parse", "main") != before
    assert git(app, "log", "-1", "--format=%s") == "Deploy again after a failed build"


class NoApps(FakePlatform):
    @override
    def status(self, sbu: str, name: str) -> m.AppStatus:
        msg = f"There is no app {sbu}-{name}."
        raise NotFoundError(msg)


def test_opening_an_app_that_does_not_exist_says_so_plainly(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="There is no app fi-bond-prices on the platform"):
        commands.open_app(context(tmp_path, NoApps([m.Build(number=1, state="succeeded")])), name="bond-prices", sbu="fi", directory=None)
    assert not (tmp_path / "bond-prices").exists()
