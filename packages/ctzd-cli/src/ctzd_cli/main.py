"""Entry point: parse the command line, run the command, print the outcome as text or JSON."""

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ctzd_cli import assets, commands, proc, releases
from ctzd_cli.commands import Context, Outcome
from ctzd_cli.errors import CliError, UsageError

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
_DEPLOY_TIMEOUT = 540.0  # under the 10-minute ceiling most agents put on one command


class Args(BaseModel):
    """Every option of every command; argparse fills the ones the chosen command has."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    command: str
    as_json: bool = False
    action: str | None = None
    name: str | None = None
    sbu: str | None = None
    ui: Literal["streamlit", "dash", "none"] = "streamlit"
    dir: Path | None = None
    owner: str | None = None
    offline: bool = False
    start: bool = False
    wait: bool = False
    no_browser: bool = False
    port: int = 8501
    job: str | None = None
    local: bool = False
    schedule: str | None = None
    size: Literal["s", "m", "l"] = "m"
    fast: bool = False
    message: str | None = None
    detach: bool = False
    timeout: float = _DEPLOY_TIMEOUT
    tail: int = 200
    target: str | None = None
    yes: bool = False
    image: str | None = None
    release: int | None = None
    out: Path | None = None
    commit: str | None = None
    url: str | None = None
    screenshot: Path | None = None


def _setup_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]", common: argparse.ArgumentParser) -> None:
    sub.add_parser("setup", parents=[common], help="check the laptop and install what is missing")
    login = sub.add_parser("login", parents=[common], help="log in to GitHub with a one-time code")
    mode = login.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", action="store_true", help="get a code for the person to approve")
    mode.add_argument("--wait", action="store_true", help="wait until the person has approved the code")
    login.add_argument("--no-browser", action="store_true", help="do not open the browser")


def _app_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]", common: argparse.ArgumentParser) -> None:
    new = sub.add_parser("new", parents=[common], help="create a new app and its GitHub repo")
    new.add_argument("name")
    new.add_argument("--sbu", required=True, help="business unit, such as fi")
    new.add_argument("--ui", choices=["streamlit", "dash", "none"], default="streamlit")
    new.add_argument("--dir", type=Path, help="parent folder; default: the current folder")
    new.add_argument("--owner", help="owner email; default: the GitHub login's email")
    new.add_argument("--offline", action="store_true", help="only write the files locally; no repo, no push")
    open_ = sub.add_parser("open", parents=[common], help="clone an existing app")
    open_.add_argument("name")
    open_.add_argument("--sbu", required=True)
    open_.add_argument("--dir", type=Path)
    dev = sub.add_parser("dev", parents=[common], help="run the dashboard locally")
    dev.add_argument("--port", type=int, default=8501)
    run = sub.add_parser("run", parents=[common], help="run a job in Airflow, or locally with --local")
    run.add_argument("job")
    run.add_argument("--local", action="store_true")
    run.add_argument("--wait", action="store_true", help="wait until the Airflow run finishes")
    run.add_argument("--timeout", type=float, default=_DEPLOY_TIMEOUT)
    sub.add_parser("sources", parents=[common], help="list the data sources the platform knows")
    add = sub.add_parser("add", parents=[common], help="add a part to the app")
    add_job = add.add_subparsers(dest="action", required=True).add_parser("job", parents=[common], help="add a scheduled job")
    add_job.add_argument("name")
    add_job.add_argument("--schedule", required=True, help="five-field cron in UTC, such as '0 7 * * 1-5'")
    add_job.add_argument("--size", choices=["s", "m", "l"], default="m")
    check = sub.add_parser("check", parents=[common], help="run every gate")
    check.add_argument("--fast", action="store_true", help="skip the tests")
    sub.add_parser("env", parents=[common], help="write read-only source credentials into .env")
    sub.add_parser("sync", parents=[common], help="rewrite platform-owned files to this ctzd version")


def _ship_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]", common: argparse.ArgumentParser) -> None:
    deploy = sub.add_parser("deploy", parents=[common], help="check, commit, push, and wait for the live URL")
    deploy.add_argument("--message", help="commit message; default: the changed files")
    deploy.add_argument("--detach", action="store_true", help="return right after pushing")
    deploy.add_argument("--timeout", type=float, default=_DEPLOY_TIMEOUT, help="seconds to wait for Jenkins")
    status = sub.add_parser("status", parents=[common], help="current release, URL, jobs, and last build")
    status.add_argument("--wait", action="store_true", help="wait for the build of the latest commit")
    status.add_argument("--timeout", type=float, default=_DEPLOY_TIMEOUT)
    logs = sub.add_parser("logs", parents=[common], help="recent dashboard or job log lines")
    logs.add_argument("--job")
    logs.add_argument("--tail", type=int, default=200)
    sub.add_parser("releases", parents=[common], help="list releases")
    rollback = sub.add_parser("rollback", parents=[common], help="restore a release and pause auto-deploy")
    rollback.add_argument("target", nargs="?", help="release such as v12; default: the one before the current")
    rollback.add_argument("--timeout", type=float, default=300.0)
    delete = sub.add_parser("delete", parents=[common], help="delete the app")
    delete.add_argument("--yes", action="store_true")


def _ci_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]", common: argparse.ArgumentParser) -> None:
    ci_sub = sub.add_parser("ci", parents=[common], help="steps the Jenkins pipeline runs").add_subparsers(dest="action", required=True)
    ci_sub.add_parser("check", parents=[common])
    ci_render = ci_sub.add_parser("render", parents=[common])
    ci_render.add_argument("--image", required=True)
    ci_render.add_argument("--release", type=int)
    ci_render.add_argument("--out", type=Path, required=True)
    ci_release = ci_sub.add_parser("release", parents=[common])
    ci_release.add_argument("--image", required=True)
    ci_release.add_argument("--commit", required=True)
    ci_smoke = ci_sub.add_parser("smoke", parents=[common])
    ci_smoke.add_argument("--url", required=True)
    ci_smoke.add_argument("--screenshot", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", dest="as_json", action="store_true", help="print the outcome as JSON (agents should always pass this)")
    parser = argparse.ArgumentParser(prog="ctzd", description="Create, check, and deploy ctzd apps.", parents=[common])
    parser.add_argument("--version", action="version", version=f"ctzd {assets.version()}")
    sub = parser.add_subparsers(dest="command", required=True)
    for add in (_setup_parsers, _app_parsers, _ship_parsers, _ci_parsers):
        add(sub, common)
    return parser


def _required[T](value: T | None, name: str) -> T:
    if value is None:
        msg = f"{name} is required."
        raise UsageError(msg)
    return value


def _stream(command: list[str], cwd: Path, env: dict[str, str]) -> int:
    """Run a long-lived command with the terminal attached, as `ctzd dev` needs."""
    return subprocess.run(command, cwd=cwd, env=proc.child_env(env), check=False).returncode


Handler = Callable[[Args, Context], Outcome]

HANDLERS: dict[str, Handler] = {
    "setup": lambda _a, ctx: commands.setup(ctx),
    "login": lambda a, ctx: commands.login_start(ctx, open_browser=not a.no_browser) if a.start else commands.login_wait(ctx),
    "new": lambda a, ctx: commands.new(ctx, name=_required(a.name, "NAME"), sbu=_required(a.sbu, "--sbu"), ui=a.ui, directory=a.dir, owner=a.owner, offline=a.offline),
    "open": lambda a, ctx: commands.open_app(ctx, name=_required(a.name, "NAME"), sbu=_required(a.sbu, "--sbu"), directory=a.dir),
    "dev": lambda a, ctx: commands.dev(ctx, port=a.port, run=_stream),
    "run": lambda a, ctx: commands.run_job(ctx, job=_required(a.job, "JOB"), local=a.local, wait=a.wait, timeout=a.timeout),
    "sources": lambda _a, ctx: commands.sources(ctx),
    "add job": lambda a, ctx: commands.add_job(ctx, name=_required(a.name, "NAME"), schedule=_required(a.schedule, "--schedule"), size=a.size),
    "check": lambda a, ctx: commands.check(ctx, fast=a.fast),
    "env": lambda _a, ctx: commands.env(ctx),
    "deploy": lambda a, ctx: commands.deploy(ctx, message=a.message, detach=a.detach, timeout=a.timeout),
    "status": lambda a, ctx: commands.status(ctx, wait=a.wait, timeout=a.timeout),
    "logs": lambda a, ctx: commands.logs(ctx, job=a.job, tail=a.tail),
    "releases": lambda _a, ctx: commands.list_releases(ctx),
    "rollback": lambda a, ctx: commands.rollback(ctx, target=a.target, timeout=a.timeout),
    "sync": lambda _a, ctx: commands.sync_files(ctx),
    "delete": lambda a, ctx: commands.delete(ctx, confirmed=a.yes),
    "ci check": lambda _a, ctx: commands.ci_check(ctx),
    "ci render": lambda a, ctx: commands.ci_render(ctx, image=_required(a.image, "--image"), release_number=a.release, out=_required(a.out, "--out")),
    "ci release": lambda a, ctx: commands.ci_release(ctx, image=_required(a.image, "--image"), commit=_required(a.commit, "--commit"), now=lambda: datetime.now(UTC)),
    "ci smoke": lambda a, ctx: commands.ci_smoke(ctx, url=_required(a.url, "--url"), screenshot=_required(a.screenshot, "--screenshot")),
}


def dispatch(command: str, args: Args, ctx: Context) -> Outcome:
    handler = HANDLERS.get(command)
    if handler is None:
        msg = f"Unknown command {command}."
        raise UsageError(msg)
    return handler(args, ctx)


def render_text(outcome: Outcome) -> str:
    lines = [outcome.summary]
    steps = outcome.data.get("steps") or outcome.data.get("checks")
    if isinstance(steps, list):
        lines.extend(f"  {step.get('name')}: {step.get('state')} {step.get('detail') or ''}".rstrip() for step in steps if isinstance(step, dict) and step.get("state") not in {"passed", "ok"})
    lines += [f"Next: {item}" for item in outcome.next_steps]
    return "\n".join(lines)


def emit(command: str, outcome: Outcome, *, as_json: bool) -> None:
    if as_json:
        document = {"ok": outcome.ok, "command": command, "summary": outcome.summary, **outcome.data, "next": outcome.next_steps}
        sys.stdout.write(json.dumps(document, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(render_text(outcome) + "\n")


def main(argv: Sequence[str] | None = None, *, ctx: Context | None = None) -> int:
    namespace = _parser().parse_args(argv)
    try:
        args = Args.model_validate(vars(namespace))
    except ValidationError as err:
        sys.stderr.write(f"ctzd: {err}\n")
        return EXIT_USAGE
    context = ctx or Context(cwd=Path.cwd(), platform_factory=commands.default_platform, releases=releases.ReleaseSource(commands.default_platform))
    command = " ".join(part for part in (args.command, args.action) if part is not None)
    try:
        hints = commands.prepare(context, command)
        outcome = dispatch(command, args, context)
    except CliError as err:
        failure = Outcome(summary=err.message, next_steps=[] if err.hint is None else [err.hint], ok=False)
        emit(command, failure, as_json=args.as_json)
        return EXIT_USAGE if isinstance(err, UsageError) else EXIT_FAILED
    except httpx.HTTPError as err:
        emit(command, Outcome(summary=f"A network request failed: {err}", ok=False), as_json=args.as_json)
        return EXIT_FAILED
    outcome.next_steps += hints
    emit(command, outcome, as_json=args.as_json)
    return EXIT_OK if outcome.ok else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
