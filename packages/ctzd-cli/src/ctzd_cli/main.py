"""Entry point: parse the command line, run the command, print the outcome as text or JSON."""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ctzd_cli import assets, commands, kitwork
from ctzd_cli.appconfig import Kind
from ctzd_cli.commands import Context, Outcome
from ctzd_cli.errors import CliError, UsageError

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


class Args(BaseModel):
    """Every option of every command; argparse fills the ones the chosen command has."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    command: str
    as_json: bool = False
    action: str | None = None
    name: str | None = None
    sbu: str | None = None
    kind: Kind = "dash"
    dir: Path | None = None
    owner: str | None = None
    offline: bool = False
    start: bool = False
    wait: bool = False
    no_browser: bool = False
    port: int | None = None
    stop: bool = False
    job: str | None = None
    local: bool = False
    fast: bool = False
    image: str | None = None
    release: int | None = None
    out: Path | None = None
    commit: str | None = None
    fake: bool = False
    argv: tuple[str, ...] = ()
    source: str | None = None
    table: str | None = None
    title: str | None = None
    kit_kind: str | None = None
    path: Path | None = None
    no_apps: bool = False
    part: str | None = None


def _setup_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser], common: argparse.ArgumentParser) -> None:
    sub.add_parser("setup", parents=[common], help="check the desktop and install what is missing")
    login = sub.add_parser("login", parents=[common], help="log in to GitHub with a one-time code")
    mode = login.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", action="store_true", help="get a code for the person to approve")
    mode.add_argument("--wait", action="store_true", help="wait until the person has approved the code")
    login.add_argument("--no-browser", action="store_true", help="do not open the browser")


def _app_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser], common: argparse.ArgumentParser) -> None:
    new = sub.add_parser("new", parents=[common], help="create a new app")
    new.add_argument("name")
    new.add_argument("--sbu", required=True, help="the department, such as fi")
    new.add_argument("--kind", choices=get_args(Kind), default="dash", help="dash: one dashboard and any jobs; job: only jobs")
    new.add_argument("--dir", type=Path, help="parent folder; default: the current folder")
    new.add_argument("--owner", help="owner email; default: the GitHub login's email")
    new.add_argument("--offline", action="store_true", help="for platform tests only: write the files with no repository, so the app can never be deployed")
    dev = sub.add_parser("dev", parents=[common], help="run the dashboard locally in the background, or restart it; --stop stops it")
    dev.add_argument("--port", type=int, help="default: 8501, or the next free port")
    dev.add_argument("--stop", action="store_true", help="stop the local dashboard")
    dev.add_argument("--fake", action="store_true", help="serve every source's sample rows instead of the real sources")
    exec_ = sub.add_parser("exec", parents=[common], help="run a command in the app with its credentials, as ctzd dev does: ctzd exec --json -- python -c '...'")
    exec_.add_argument("argv", nargs=argparse.REMAINDER, help="the command, after --")
    run = sub.add_parser("run", parents=[common], help="run a job; --local runs it on this desktop")
    run.add_argument("job")
    run.add_argument("--local", action="store_true")
    sources = sub.add_parser("sources", parents=[common], help="the kit's sources and loaders; with SOURCE, its tables; with TABLE, its columns and first rows")
    sources.add_argument("source", nargs="?", help="a source, as `ctzd sources` lists it")
    sources.add_argument("table", nargs="?", help="a table or view, as `ctzd sources SOURCE` lists it")
    check = sub.add_parser("check", parents=[common], help="run every gate")
    check.add_argument("--fast", action="store_true", help="skip the tests")
    sub.add_parser("sync", parents=[common], help="rewrite the build files and guidance from this ctzd")


def _kit_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser], common: argparse.ArgumentParser) -> None:
    kit_sub = sub.add_parser("kit", parents=[common], help="for kit maintainers: start a kit, add a source, prove the contract").add_subparsers(dest="action", required=True)
    new = kit_sub.add_parser("new", parents=[common], help="start kits/SBU")
    new.add_argument("name", metavar="SBU")
    new.add_argument("--title", required=True, help="the department's name, such as 'Fixed Income'")
    new.add_argument("--dir", type=Path, help="the folder of kits; default: the current folder")
    add = kit_sub.add_parser("add", parents=[common], help="add a part to the kit").add_subparsers(dest="part", required=True)
    source = add.add_parser("source", parents=[common], help="add a source with a working loader, sample rows and a test")
    source.add_argument("name")
    source.add_argument("--kind", dest="kit_kind", required=True, choices=kitwork.SOURCE_KINDS)
    source.add_argument("--dir", type=Path, help="the kit's folder; default: the kit the current folder is in")
    check = kit_sub.add_parser("check", parents=[common], help="prove the kit meets the contract")
    check.add_argument("path", nargs="?", type=Path, help="the kit's folder; default: the kit the current folder is in")
    check.add_argument("--no-apps", action="store_true", help="skip making and checking an app of each template on the kit")


def _ci_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser], common: argparse.ArgumentParser) -> None:
    ci_sub = sub.add_parser("ci", parents=[common], help="the steps the reusable workflow runs").add_subparsers(dest="action", required=True)
    ci_sub.add_parser("check", parents=[common])
    ci_render = ci_sub.add_parser("render", parents=[common])
    ci_render.add_argument("--image", required=True)
    ci_render.add_argument("--release", type=int)
    ci_render.add_argument("--out", type=Path, required=True)
    ci_release = ci_sub.add_parser("release", parents=[common])
    ci_release.add_argument("--image", required=True)
    ci_release.add_argument("--commit", required=True)


def _parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(add_help=False)
    top.add_argument("--json", dest="as_json", action="store_true", help="print the outcome as JSON (agents should always pass this)")
    # Subcommands accept --json too; their default must not overwrite a --json given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", dest="as_json", action="store_true", default=argparse.SUPPRESS, help="print the outcome as JSON (agents should always pass this)")
    parser = argparse.ArgumentParser(prog="ctzd", description="Create, check, and deploy ctzd apps.", parents=[top])
    parser.add_argument("--version", action="version", version=f"ctzd {assets.version()}")
    sub = parser.add_subparsers(dest="command", required=True)
    for add in (_setup_parsers, _app_parsers, _kit_parsers, _ci_parsers):
        add(sub, common)
    return parser


def _required[T](value: T | None, name: str) -> T:
    if value is None:
        msg = f"{name} is required."
        raise UsageError(msg)
    return value


Handler = Callable[[Args, Context], Outcome]

HANDLERS: dict[str, Handler] = {
    "setup": lambda _a, ctx: commands.setup(ctx),
    "login": lambda a, ctx: commands.login_start(ctx, open_browser=not a.no_browser) if a.start else commands.login_wait(ctx),
    "new": lambda a, ctx: commands.new(ctx, name=_required(a.name, "NAME"), sbu=_required(a.sbu, "--sbu"), kind=a.kind, directory=a.dir, owner=a.owner, offline=a.offline),
    "dev": lambda a, ctx: commands.dev(ctx, port=a.port, stop=a.stop, fake=a.fake),
    "run": lambda a, ctx: commands.run_job(ctx, job=_required(a.job, "JOB"), local=a.local),
    "exec": lambda a, ctx: commands.exec_command(ctx, argv=a.argv),
    "check": lambda a, ctx: commands.check(ctx, fast=a.fast),
    "sources": lambda a, ctx: commands.sources(ctx, source=a.source, table=a.table),
    "kit new": lambda a, ctx: commands.kit_new(ctx, sbu=_required(a.name, "SBU"), title=_required(a.title, "--title"), directory=a.dir),
    "kit add source": lambda a, ctx: commands.kit_add_source(ctx, name=_required(a.name, "NAME"), kind=_required(a.kit_kind, "--kind"), directory=a.dir),
    "kit check": lambda a, ctx: commands.kit_check(ctx, directory=a.path, make_apps=not a.no_apps),
    "sync": lambda _a, ctx: commands.sync_files(ctx),
    "ci check": lambda _a, ctx: commands.ci_check(ctx),
    "ci render": lambda a, ctx: commands.ci_render(ctx, image=_required(a.image, "--image"), release_number=a.release, out=_required(a.out, "--out")),
    "ci release": lambda a, ctx: commands.ci_release(ctx, image=_required(a.image, "--image"), commit=_required(a.commit, "--commit"), now=lambda: datetime.now(UTC)),
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
    context = ctx or Context(cwd=Path.cwd())
    command = " ".join(part for part in (args.command, args.action, args.part) if part is not None)
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
