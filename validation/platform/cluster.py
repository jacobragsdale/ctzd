"""Local stand-ins for GitHub, Jenkins, the cluster, and Airflow, behind the platform API's gateway protocols.

Repositories are bare git repositories on disk. A builder thread notices every
new commit on `main` and runs what `ctzdApp()` runs, with the real CLI steps:
`ctzd ci check`, a deploy that starts the dashboard as a local process and
keeps the old one until the new one is ready, `ctzd ci smoke`, and
`ctzd ci release`. Jobs run on demand with `python -m app.jobs.<job>`.
"""

import dataclasses
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ctzd_api.gateways import AppState, JenkinsBuild, Permission
from ctzd_api.service import DELETE_JOB, ROLLBACK_JOB
from ctzd_cli.render import AirflowSpec, HelmValues
from validation.harness import CTZD, tool

_READY_SECONDS = 120.0
_POLL_SECONDS = 2.0
_PAUSED = "checks passed; auto-deploy is paused after a rollback"
_LOG = logging.getLogger(__name__)


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([tool("git"), "-c", "user.name=ctzd", "-c", "user.email=ctzd@localhost", *args], cwd=cwd, capture_output=True, text=True, check=False)


@dataclass
class LocalGitHub:
    """Bare repositories under `root`; every known login is an org member and admin of every app."""

    root: Path
    tokens: dict[str, str]

    def bare(self, repo: str) -> Path:
        return self.root / f"{repo}.git"

    def login_for(self, user_token: str) -> str | None:
        return self.tokens.get(user_token)

    def is_org_member(self, login: str) -> bool:
        return login in self.tokens.values()

    def permission(self, repo: str, login: str) -> Permission:
        return "admin" if self.repo_exists(repo) and self.is_org_member(login) else "none"

    def repo_exists(self, repo: str) -> bool:
        return self.bare(repo).is_dir()

    def create_repo(self, repo: str, *, admin: str, team: str) -> None:
        del admin, team
        _git("init", "--quiet", "--bare", "--initial-branch=main", str(self.bare(repo)))

    def archive_repo(self, repo: str) -> None:
        target = self.root / "archived" / f"{repo}.git"
        target.parent.mkdir(parents=True, exist_ok=True)
        self.bare(repo).rename(target)

    def file(self, repo: str, path: str) -> str | None:
        shown = _git("--git-dir", str(self.bare(repo)), "show", f"main:{path}")
        return shown.stdout if shown.returncode == 0 else None

    def release_tags(self, repo: str) -> list[tuple[str, str]]:
        listed = _git("--git-dir", str(self.bare(repo)), "for-each-ref", "refs/tags/ctzd/v*", "--format=%(refname:strip=2)\x1f%(contents)\x1e")
        return [(name.strip(), body) for record in listed.stdout.split("\x1e") if "\x1f" in record for name, body in [record.split("\x1f", 1)]]

    def heads(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for bare in sorted(self.root.glob("*.git")):
            head = _git("--git-dir", str(bare), "rev-parse", "--verify", "--quiet", "refs/heads/main")
            if head.returncode == 0:
                found[bare.name.removesuffix(".git")] = head.stdout.strip()
        return found


@dataclass
class Dashboard:
    process: subprocess.Popen[bytes]
    port: int
    workspace: Path
    log: Path


@dataclass
class JobRun:
    dag_id: str
    state: str = "queued"
    log: str = ""


@dataclass
class AppRecord:
    builds: list[JenkinsBuild] = field(default_factory=list)
    consoles: dict[int, str] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    state: AppState = field(default_factory=lambda: AppState(release=None, paused=False))
    dashboard: Dashboard | None = None
    workspaces: dict[int, Path] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        address: object = probe.getsockname()
    if not isinstance(address, tuple) or not isinstance(port := address[1], int):
        msg = f"unexpected socket address {address!r}"
        raise TypeError(msg)
    return port


@dataclass
class LocalCluster:
    """Builds, dashboards, and job runs for every app; the Jenkins, Kube, and Airflow adapters read and drive it."""

    github: LocalGitHub
    root: Path
    ci_env: dict[str, str]
    runtime_env: Callable[[list[str]], dict[str, str]]
    # Fault injection: this build number of every app fails at the Image stage, as a registry outage would.
    fail_build: int | None = None
    apps: dict[str, AppRecord] = field(default_factory=dict)
    jobs: dict[str, tuple[Path, list[str], dict[str, str]]] = field(default_factory=dict)
    runs: dict[str, JobRun] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop: threading.Event = field(default_factory=threading.Event)
    _pool: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=3, thread_name_prefix="build"))

    def record(self, repo: str) -> AppRecord:
        with self._lock:
            return self.apps.setdefault(repo, AppRecord())

    # ------------------------------------------------------------ the builder

    def start(self) -> None:
        threading.Thread(target=self._watch, daemon=True, name="watch").start()

    def stop(self) -> None:
        self._stop.set()
        for record in self.apps.values():
            if record.dashboard is not None:
                _terminate(record.dashboard)

    def _watch(self) -> None:
        while not self._stop.is_set():
            for repo, head in self.github.heads().items():
                record = self.record(repo)
                if head not in record.seen:
                    record.seen.add(head)
                    self._pool.submit(self._build, repo, head).add_done_callback(_report_crash)
            self._stop.wait(_POLL_SECONDS)

    def _ctzd(self, workspace: Path, *args: str, console: list[str]) -> tuple[int, dict[str, object]]:
        result = subprocess.run([str(CTZD), *args, "--json"], cwd=workspace, env=os.environ | self.ci_env, capture_output=True, text=True, check=False, timeout=1800)
        console.append(f"$ ctzd {' '.join(args)}\n{result.stdout}{result.stderr}")
        try:
            parsed: object = json.loads(result.stdout)
        except json.JSONDecodeError:
            parsed = {}
        return result.returncode, {str(key): value for key, value in parsed.items()} if isinstance(parsed, dict) else {}

    def _build(self, repo: str, commit: str) -> None:
        record = self.record(repo)
        with record.lock:
            number = len(record.builds) + 1
            url = f"http://jenkins.local/job/ctzd-apps/job/{repo}/job/main/{number}/"
            record.builds.append(JenkinsBuild(number, commit, building=True, result=None, url=url, description=None, failed_stage=None))
            console: list[str] = []
            try:
                result, stage, description = self._pipeline(repo, record, number, commit, console)
            except (OSError, subprocess.SubprocessError, httpx.HTTPError) as err:
                result, stage, description = "FAILURE", "Prepare", None
                console.append(f"pipeline error: {err!r}")
            record.consoles[number] = "\n".join(console)
            (self.root / "builds" / repo / f"{number}.console.log").write_text(record.consoles[number])
            record.builds[number - 1] = JenkinsBuild(number, commit, building=False, result=result, url=url, description=description, failed_stage=stage)

    def _pipeline(self, repo: str, record: AppRecord, number: int, commit: str, console: list[str]) -> tuple[str, str | None, str | None]:
        workspace = self.root / "builds" / repo / str(number)
        shutil.rmtree(workspace, ignore_errors=True)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        cloned = _git("clone", "--quiet", str(self.github.bare(repo)), str(workspace))
        checked_out = _git("checkout", "--quiet", commit, cwd=workspace)
        if cloned.returncode != 0 or checked_out.returncode != 0:
            console.append(cloned.stderr + checked_out.stderr)
            return "FAILURE", "Prepare", None
        code, _ = self._ctzd(workspace, "ci", "check", console=console)
        if code != 0:
            return "FAILURE", "Checks", None
        if record.state.paused:
            return "SUCCESS", None, _PAUSED
        if number == self.fail_build:
            console.append("#12 pushing contosoctzd.azurecr.io/apps/image\nerror: failed to push: 503 Service Unavailable: the registry is temporarily unavailable")
            return "FAILURE", "Image", None
        return self._deploy(repo, record, commit=commit, workspace=workspace, console=console)

    def _deploy(self, repo: str, record: AppRecord, *, commit: str, workspace: Path, console: list[str]) -> tuple[str, str | None, str | None]:
        image = f"local/{repo}:{commit[:12]}"
        code, _ = self._ctzd(workspace, "ci", "render", "--image", image, "--out", str(workspace / ".out"), console=console)
        if code != 0:
            return "FAILURE", "Deploy", None
        values = HelmValues.model_validate_json((workspace / ".out" / "helm-values.json").read_bytes())
        spec = AirflowSpec.model_validate_json((workspace / ".out" / "airflow-spec.json").read_bytes())
        if values.enabled:
            failed = self._roll_out(repo, record, workspace=workspace, values=values, console=console)
            if failed is not None:
                return "FAILURE", failed, None
        self._register_jobs(workspace, spec)
        code, released = self._ctzd(workspace, "ci", "release", "--image", image, "--commit", commit, console=console)
        number_released = released.get("release")
        if code != 0 or not isinstance(number_released, int):
            return "FAILURE", "Release", None
        record.workspaces[number_released] = workspace
        record.state = AppState(release=number_released, paused=False)
        return "SUCCESS", None, f"release v{number_released}"

    def _roll_out(self, repo: str, record: AppRecord, *, workspace: Path, values: HelmValues, console: list[str]) -> str | None:
        """Start the new dashboard and smoke-test it; on failure the previous one keeps serving. Returns the failed stage."""
        previous = record.dashboard
        started = self._start_dashboard(repo, workspace, values, console)
        if started is None:
            return "Deploy"  # like helm --atomic: the old dashboard keeps serving
        record.dashboard = started
        code, _ = self._ctzd(workspace, "ci", "smoke", "--url", f"http://127.0.0.1:{started.port}", console=console)
        if code != 0:
            _terminate(started)
            record.dashboard = previous  # like helm rollback: the previous release keeps serving
            console.append("The dashboard showed an error after the rollout; the previous release is back.")
            return "Smoke test"
        if previous is not None:
            _terminate(previous)
        return None

    def _start_dashboard(self, repo: str, workspace: Path, values: HelmValues, console: list[str]) -> Dashboard | None:
        port = free_port()
        command = [part.replace("--server.port=8080", f"--server.port={port}").replace("--bind=0.0.0.0:8080", f"--bind=127.0.0.1:{port}") for part in values.command]
        env = os.environ | values.env | self.runtime_env(list(values.source_secrets)) | {"PATH": f"{workspace / '.venv' / 'bin'}:{os.environ['PATH']}"}
        log = workspace / "dashboard.log"
        with log.open("wb") as out:
            process = subprocess.Popen(command, cwd=workspace, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        dashboard = Dashboard(process=process, port=port, workspace=workspace, log=log)
        path = values.readiness_path
        deadline = time.monotonic() + _READY_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if _answers(f"http://127.0.0.1:{port}{path}"):
                console.append(f"{repo}: dashboard ready on port {port}")
                return dashboard
            time.sleep(1)
        console.append(f"{repo}: the dashboard never became ready; the previous release keeps serving.\n{log.read_text(errors='replace')[-3000:]}")
        _terminate(dashboard)
        return None

    def _register_jobs(self, workspace: Path, spec: AirflowSpec) -> None:
        env = spec.env | self.runtime_env(list(spec.source_secrets))
        for job in spec.jobs:
            self.jobs[job.dag_id] = (workspace, list(job.command), env)

    # ------------------------------------------------------------ rollback, delete, jobs

    def rollback(self, repo: str, release: int) -> None:
        record = self.record(repo)
        with record.lock:
            workspace = record.workspaces.get(release)
            if workspace is not None:
                values = HelmValues.model_validate_json((workspace / ".out" / "helm-values.json").read_bytes())
                if values.enabled:
                    started = self._start_dashboard(repo, workspace, values, [])
                    if started is not None:
                        if record.dashboard is not None:
                            _terminate(record.dashboard)
                        record.dashboard = started
            record.state = AppState(release=release, paused=True)

    def delete(self, repo: str) -> None:
        record = self.record(repo)
        if record.dashboard is not None:
            _terminate(record.dashboard)
            record.dashboard = None
        sbu, _, name = repo.partition("-")
        for dag_id in [dag for dag in self.jobs if dag.startswith(f"{sbu}__{name.replace('-', '_')}__")]:
            del self.jobs[dag_id]

    def trigger_job(self, dag_id: str) -> str:
        if dag_id not in self.jobs:
            msg = f"DAG {dag_id} does not exist"
            raise httpx.HTTPStatusError(msg, request=httpx.Request("POST", "http://airflow.local"), response=httpx.Response(404))
        run_id = f"manual__{len(self.runs) + 1}"
        self.runs[run_id] = JobRun(dag_id)
        threading.Thread(target=self._run_job, args=(run_id,), daemon=True).start()
        return run_id

    def _run_job(self, run_id: str) -> None:
        run = self.runs[run_id]
        workspace, command, env = self.jobs[run.dag_id]
        run.state = "running"
        venv = workspace / ".venv" / "bin"
        result = subprocess.run(command, cwd=workspace, env=os.environ | env | {"PATH": f"{venv}:{os.environ['PATH']}"}, capture_output=True, text=True, check=False, timeout=3600)
        run.log = result.stdout + result.stderr
        run.state = "success" if result.returncode == 0 else "failed"


def _report_crash(future: Future[None]) -> None:
    error = future.exception()
    if error is not None:
        _LOG.error("a build crashed", exc_info=error)


def _answers(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code == httpx.codes.OK
    except httpx.HTTPError:
        return False


def _terminate(dashboard: Dashboard) -> None:
    if dashboard.process.poll() is None:
        os.killpg(dashboard.process.pid, signal.SIGTERM)
        try:
            dashboard.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(dashboard.process.pid, signal.SIGKILL)


@dataclass
class LocalJenkins:
    cluster: LocalCluster

    def builds(self, repo: str) -> list[JenkinsBuild]:
        return list(self.cluster.record(repo).builds)

    def console_tail(self, build: JenkinsBuild, lines: int) -> str:
        for record in self.cluster.apps.values():
            if build in record.builds:
                return "\n".join(record.consoles.get(build.number, "").splitlines()[-lines:])
        return ""

    def trigger(self, job: str, parameters: dict[str, str]) -> None:
        if job == ROLLBACK_JOB:
            threading.Thread(target=self.cluster.rollback, args=(parameters["REPO"], int(parameters["RELEASE"])), daemon=True).start()
        elif job == DELETE_JOB:
            self.cluster.delete(parameters["REPO"])


@dataclass
class LocalKube:
    cluster: LocalCluster

    def state(self, fullname: str) -> AppState:
        return self.cluster.record(fullname).state

    def set_paused(self, fullname: str, *, paused: bool) -> None:
        record = self.cluster.record(fullname)
        record.state = dataclasses.replace(record.state, paused=paused)

    def dashboard_log(self, fullname: str, tail: int) -> str:
        dashboard = self.cluster.record(fullname).dashboard
        if dashboard is None or not dashboard.log.is_file():
            return ""
        return "\n".join(dashboard.log.read_text(errors="replace").splitlines()[-tail:])


@dataclass
class LocalAirflow:
    cluster: LocalCluster

    def trigger(self, dag_id: str) -> str:
        return self.cluster.trigger_job(dag_id)

    def run_state(self, dag_id: str, run_id: str) -> str:
        del dag_id
        run = self.cluster.runs.get(run_id)
        return "queued" if run is None else run.state

    def latest_task_log(self, dag_id: str, tail: int) -> str:
        runs = [run for run in self.cluster.runs.values() if run.dag_id == dag_id]
        return "\n".join(runs[-1].log.splitlines()[-tail:]) if runs else ""
