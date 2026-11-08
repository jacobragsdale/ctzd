"""Stage 1: the real ctzd CLI against the real platform API over HTTP, the API running on seeded fakes.

    uv run python -m validation.stage1_http [WORK_DIR]

Every step runs the installed `ctzd` as a subprocess with `--json`, the way an
agent does, and checks what it printed and what reached the fakes.
"""

import datetime as dt
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import override

import uvicorn

from ctzd_api import fakes
from ctzd_api.app import create_app
from ctzd_api.gateways import AppState, JenkinsBuild
from ctzd_api.releases import ReleaseStore
from ctzd_api.service import DELETE_JOB, ROLLBACK_JOB, Services
from ctzd_cli import config, release, releases
from validation.harness import Report, build_wheels, platform_config, run_ctzd, tool

APP = "bond-prices"
REPO_NAME = "fi-bond-prices"
SEED = releases.seed_files()
LIVE = 3  # the release the fake cluster runs at the start
BEFORE_LIVE = 2
DEPLOYED = 4
FAST = 20.0  # seconds within which a command must give up on an unreachable platform
BUILD_URL = f"https://jenkins.contoso.com/job/ctzd-apps/job/{REPO_NAME}/job/main/12/"


def release_archive(number: int, *, files: dict[str, str] | None = None, requires_cli: str | None = None) -> bytes:
    manifest = SEED["release.toml"].replace("release = 1\n", f"release = {number}\n", 1)
    if requires_cli is not None:
        manifest = re.sub(r'^requires_cli = ".*"$', f'requires_cli = "{requires_cli}"', manifest, count=1, flags=re.MULTILINE)
    return fakes.archive(SEED | {"release.toml": manifest} | (files or {}))


def channels(general: int, *, cli: str | None = None) -> str:
    head = "" if cli is None else f'cli = "{cli}"\n'
    return f"{head}[channels]\npilot = {general}\ngeneral = {general}\n"


class RollbackJenkins(fakes.FakeJenkins):
    """Jenkins whose rollback job does to the cluster state what the real one does."""

    def __init__(self, kube: fakes.FakeKube) -> None:
        super().__init__()
        self.kube = kube

    @override
    def trigger(self, job: str, parameters: dict[str, str]) -> None:
        super().trigger(job, parameters)
        if job == ROLLBACK_JOB:
            self.kube.states[parameters["REPO"]] = AppState(release=int(parameters["RELEASE"]), paused=True)


def app_toml() -> str:
    return f'[app]\nname = "{APP}"\nsbu = "fi"\nui = "react"\nowners = ["ann.lee@contoso.com"]\n\n[sources]\nuse = ["fi_pricing"]\n\n[[jobs]]\nname = "refresh"\nschedule = "0 6 * * *"\n'


def tag(number: int) -> tuple[str, str]:
    record = release.Release(release=number, commit=f"c{number}", image=f"i:{number}", created_at=dt.datetime(2026, 11, 4, tzinfo=dt.UTC), app_toml=app_toml())
    return (release.tag_name(number), release.message(record))


@dataclass
class World:
    """The API's gateways, kept as their concrete fakes so steps can arrange and inspect them."""

    github: fakes.FakeGitHub
    jenkins: RollbackJenkins
    kube: fakes.FakeKube
    releases: fakes.FakeReleases
    services: Services


def seeded() -> World:
    kube = fakes.FakeKube(states={REPO_NAME: AppState(release=LIVE, paused=False)}, logs={REPO_NAME: "Uvicorn running\nGET / 200\n"})
    repo = fakes.FakeRepo(permissions={"ann": "admin", "bob": "read"}, files={"app.toml": app_toml()}, tags=[tag(1), tag(BEFORE_LIVE), tag(LIVE)])
    github = fakes.FakeGitHub(tokens={"ann-token": "ann", "bob-token": "bob"}, members={"ann", "bob"}, repos={REPO_NAME: repo})
    jenkins = RollbackJenkins(kube)
    gateway = fakes.FakeReleases(channels=channels(1), archives={1: release_archive(1)})
    services = Services(
        github=github,
        jenkins=jenkins,
        airflow=fakes.FakeAirflow(states={"manual__1": "running", "manual__2": "success"}, logs={"fi__bond_prices__refresh": "refreshed 42 rows\n"}),
        kube=kube,
        secrets=fakes.FakeSecrets(values={"ctzd-source-fi-pricing": '{"kind":"oracle","dsn":"db:1521/FI","user":"ro","password":"pw"}'}),
        releases=ReleaseStore(gateway, ttl=0.0),
        platform=config.load(),
    )
    return World(github=github, jenkins=jenkins, kube=kube, releases=gateway, services=services)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        address: object = probe.getsockname()
    if not isinstance(address, tuple) or not isinstance(port := address[1], int):
        msg = f"unexpected socket address {address!r}"
        raise TypeError(msg)
    return port


def serve(services: Services, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(create_app(services), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.1)
    return server


def git(cwd: Path, *args: str) -> str:
    return subprocess.run([tool("git"), "-c", "user.name=v", "-c", "user.email=v@contoso.com", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def stale(app: Path) -> None:
    """Forget when guidance was last checked, so the next command asks the platform again."""
    (app / ".ctzd" / "guidance.json").unlink(missing_ok=True)


@dataclass(frozen=True)
class Run:
    report: Report
    world: World
    work: Path
    app: Path
    env: dict[str, str]


def make_app(report: Report, work: Path, env: dict[str, str]) -> Path:
    created = run_ctzd(["new", APP, "--sbu", "fi", "--offline", "--owner", "ann.lee@contoso.com", "--dir", str(work / "apps")], cwd=work, env=env)
    app = work / "apps" / APP
    report.expect("new --offline writes the app on the seed release", ok=created.ok and (app / ".ctzd" / "version").read_text() == "1\n", seen=created.raw)
    (app / "app.toml").write_text(app_toml())
    synced = run_ctzd(["sync"], cwd=app, env=env)
    report.expect("sync regenerates .env.example from app.toml", ok=synced.ok and "CTZD_SOURCE_FI_PRICING=" in (app / ".env.example").read_text(), seen=synced.raw)
    git(work, "init", "--quiet", "--bare", "--initial-branch=main", "remote.git")
    git(app, "remote", "add", "origin", str(work / "remote.git"))
    git(app, "add", "--all")
    git(app, "commit", "--quiet", "-m", "Add a source and a job")
    git(app, "push", "--quiet", "origin", "main")
    return app


def everyday_commands(run: Run) -> None:
    report, app, env = run.report, run.app, run.env
    status = run_ctzd(["status"], cwd=app, env=env)
    report.expect("status reports the live release and the job's DAG link", ok=status.ok and status.body.get("release") == LIVE and "refresh" in status.text("dags"), seen=status.raw)
    sources = run_ctzd(["sources"], cwd=app, env=env)
    report.expect("sources lists names only", ok=sources.body.get("sources") == ["fi_pricing"], seen=sources.raw)
    written = run_ctzd(["env"], cwd=app, env=env)
    dotenv = (app / ".env").read_text() if (app / ".env").is_file() else ""
    report.expect("env writes the source's credentials into .env and never prints them", ok=written.ok and "CTZD_SOURCE_FI_PRICING=" in dotenv and '"pw"' not in written.raw, seen=written.raw)
    report.expect(".env stays out of git", ok=".env" not in git(app, "status", "--porcelain"))
    logs = run_ctzd(["logs"], cwd=app, env=env)
    report.expect("logs returns the dashboard's lines", ok="GET / 200" in logs.text("text"), seen=logs.raw)
    job_logs = run_ctzd(["logs", "--job", "refresh"], cwd=app, env=env)
    report.expect("logs --job returns the job's Airflow log", ok="42 rows" in job_logs.text("text"), seen=job_logs.raw)
    started = run_ctzd(["run", "refresh"], cwd=app, env=env)
    report.expect("run starts a DAG run and links to it", ok=started.ok and started.text("url").endswith("/dags/fi__bond_prices__refresh/runs/manual__1"), seen=started.raw)
    waited = run_ctzd(["run", "refresh", "--wait"], cwd=app, env=env)
    report.expect("run --wait follows the run to success", ok=waited.ok and waited.body.get("state") == "success", seen=waited.raw)
    rendered = run_ctzd(["ci", "render", "--image", "reg/fi-bond-prices:abc", "--out", str(run.work / "out")], cwd=app, env=env)
    values = (run.work / "out" / "helm-values.json").read_text() if rendered.ok else ""
    report.expect("ci render gives the pod only its own source's secret", ok='"ctzd-source-fi-pricing"' in values, seen=rendered.raw)


def releases_follow_the_channel(run: Run) -> None:
    report, app, env, gateway = run.report, run.app, run.env, run.world.releases
    gateway.archives[2] = release_archive(2, files={"guidance/AGENTS.md": "# Instructions, release 2\n"})
    gateway.channels = channels(2)
    stale(app)
    refreshed = run_ctzd(["status"], cwd=app, env=env)
    report.expect("any app command rewrites AGENTS.md from the channel's release", ok=refreshed.ok and (app / "AGENTS.md").read_text() == "# Instructions, release 2\n", seen=refreshed.raw)
    checked = run_ctzd(["check", "--fast"], cwd=app, env=env)
    report.expect("a guidance-only release leaves .ctzd/version alone", ok=checked.ok and (app / ".ctzd" / "version").read_text() == "1\n", seen=checked.raw)
    report.expect("refreshed guidance leaves nothing to commit", ok=git(app, "status", "--porcelain") == "", seen=git(app, "status", "--porcelain"))
    ruff = SEED["locked/ruff.toml"] + "# release 3\n"
    gateway.archives[3] = release_archive(3, files={"locked/ruff.toml": ruff})
    gateway.channels = channels(3, cli="9.9.9")
    stale(app)
    upgraded = run_ctzd(["check", "--fast"], cwd=app, env=env)
    moved = (app / ".ctzd" / "version").read_text() == "3\n" and (app / "ruff.toml").read_text() == ruff
    report.expect("check moves the build files to the release with new ones", ok=upgraded.ok and moved, seen=upgraded.raw)
    report.expect("a newer published ctzd is only a hint", ok=upgraded.ok and any("9.9.9" in step for step in upgraded.next_steps()), seen=upgraded.raw)
    gateway.archives[5] = release_archive(5, requires_cli="99.0")
    gateway.channels = channels(5)
    stale(app)
    blocked = run_ctzd(["status"], cwd=app, env=env)
    report.expect(
        "a release too new for this ctzd stops the command and says how to upgrade",
        ok=blocked.code == 1 and "needs ctzd 99.0" in blocked.text("summary") and "uv tool upgrade ctzd" in blocked.raw,
        seen=blocked.raw,
    )
    gateway.channels = channels(3)
    stale(app)


def shipping(run: Run) -> None:
    report, app, env, world = run.report, run.app, run.env, run.world
    world.kube.states[REPO_NAME] = AppState(release=LIVE, paused=True)
    (app / "README.md").write_text("# Bond prices\n\nUpdated.\n")
    pushed = run_ctzd(["deploy", "--detach"], cwd=app, env=env)
    subjects = git(app, "log", "-3", "--format=%s").splitlines()
    report.expect(
        "deploy commits the platform upgrade on its own, then the change",
        ok=pushed.ok and subjects[:2] == ["Update README.md", "Upgrade platform files to platform release 3"],
        seen=(pushed.raw, subjects),
    )
    report.expect("deploy cleared the rollback pause", ok=not world.kube.state(REPO_NAME).paused)
    commit = pushed.text("commit")
    world.jenkins.by_repo[REPO_NAME] = [
        JenkinsBuild(12, commit, building=False, result="SUCCESS", url=BUILD_URL, description="checks passed; auto-deploy is paused after a rollback", failed_stage=None)
    ]
    nothing = run_ctzd(["status", "--wait"], cwd=app, env=env)
    report.expect("a build that deployed nothing is not reported as live", ok=not nothing.ok and "nothing was deployed" in nothing.text("summary"), seen=nothing.raw)
    world.jenkins.by_repo[REPO_NAME] = [JenkinsBuild(13, commit, building=False, result="SUCCESS", url=BUILD_URL, description=f"release v{DEPLOYED}", failed_stage=None)]
    live = run_ctzd(["status", "--wait"], cwd=app, env=env)
    report.expect("a deployed build reports its release and URL", ok=live.ok and f"v{DEPLOYED} is live" in live.text("summary") and live.text("url").startswith(f"https://{REPO_NAME}."), seen=live.raw)
    tagged = run_ctzd(["ci", "release", "--image", "reg/fi-bond-prices:abc", "--commit", commit], cwd=app, env=env)
    listed = run_ctzd(["releases"], cwd=app, env=env)
    report.expect("ci release tags the commit and releases lists it", ok=tagged.ok and "v1" in listed.text("summary"), seen=(tagged.raw, listed.raw))
    world.kube.states[REPO_NAME] = AppState(release=LIVE, paused=False)
    rolled = run_ctzd(["rollback"], cwd=app, env=env)
    report.expect("rollback picks the release before the live one and waits for it", ok=rolled.ok and rolled.body.get("release") == BEFORE_LIVE and rolled.body.get("done") is True, seen=rolled.raw)


def refusals_and_outages(run: Run, server: uvicorn.Server) -> None:
    report, app, env, world = run.report, run.app, run.env, run.world
    refused = run_ctzd(["status"], cwd=app, env=env | {"CTZD_GITHUB_TOKEN": "not-a-token"})
    report.expect("a token the platform refuses says to log in again", ok=refused.code == 1 and "ctzd login" in refused.raw, seen=refused.raw)
    reader = run_ctzd(["rollback"], cwd=app, env=env | {"CTZD_GITHUB_TOKEN": "bob-token"})
    report.expect("read access cannot roll back", ok=reader.code == 1 and "do not have access" in reader.raw, seen=reader.raw)
    deleted = run_ctzd(["delete", "--yes"], cwd=app, env=env)
    report.expect("delete archives the repo and starts the delete job", ok=deleted.ok and world.github.repos[REPO_NAME].archived and world.jenkins.triggered[-1][0] == DELETE_JOB, seen=deleted.raw)
    server.should_exit = True
    time.sleep(1)
    stale(app)
    began = time.monotonic()
    down = run_ctzd(["status"], cwd=app, env=env)
    report.expect("with the platform down, a command fails fast and says so", ok=down.code == 1 and "could not be reached" in down.raw and time.monotonic() - began < FAST, seen=down.raw)
    stale(app)
    offline = run_ctzd(["check", "--fast"], cwd=app, env=env)
    report.expect("with the platform down, check passes on the cached release and says guidance may be stale", ok=offline.ok and "could not be refreshed" in offline.raw, seen=offline.raw)


def main() -> int:
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="ctzd-stage1-"))
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    report = Report("stage 1 (CLI and API over HTTP)")
    world = seeded()
    port = free_port()
    server = serve(world.services, port)
    report.expect("the API starts", ok=server.started)
    cfg = platform_config(work, build_wheels(work / "wheels"), api_url=f"http://127.0.0.1:{port}")
    env = {"CTZD_PLATFORM_CONFIG": str(cfg), "CTZD_HOME": str(work / "home"), "CTZD_GITHUB_TOKEN": "ann-token"}
    run = Run(report=report, world=world, work=work, app=make_app(report, work, env), env=env)
    everyday_commands(run)
    releases_follow_the_channel(run)
    shipping(run)
    refusals_and_outages(run, server)
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main())
