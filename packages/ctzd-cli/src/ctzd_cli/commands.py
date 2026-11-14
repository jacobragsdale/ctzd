"""The ctzd commands. Each returns an Outcome; `main` prints it as text or JSON."""

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, assert_never

from ctzd_cli import appconfig, assets, auth, checks, config, devserver, gitops, kitfacts, kitwork, proc, release, render, scaffold, setup_checks, sync
from ctzd_cli.appconfig import AppConfig, AppSection, Job, Kind, SourcesSection
from ctzd_cli.errors import CliError, UsageError

ENV_FILE = ".env"
_TEMPLATE_JOB = Job(name="daily_positions", schedule="0 7 * * 1-5", timezone="Europe/London")
# Commands that work inside an app; each brings the app's platform files to this CLI before it runs.
APP_COMMANDS = frozenset({"dev", "run", "exec", "check", "sync", "sources"})
NOT_BUILT = "is not built yet: it arrives with the GitHub side of the platform (PLAN.md section 15, step 7)"


@dataclass
class Outcome:
    summary: str
    data: dict[str, object] = field(default_factory=dict)
    next_steps: list[str] = field(default_factory=list)
    ok: bool = True


class CheckRunner(Protocol):
    def __call__(self, target: checks.Target, /, *, mode: checks.Mode, fast: bool) -> list[checks.Step]: ...


@dataclass(frozen=True)
class Context:
    """What every command needs: where it runs, and the seams tests replace."""

    cwd: Path
    token: Callable[[], str] = auth.require_token
    run_checks: CheckRunner = checks.run_all
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = auth.utc_now


def _app(ctx: Context) -> tuple[Path, AppConfig]:
    root = appconfig.find_root(ctx.cwd)
    return root, appconfig.load(root)


def _target(root: Path, app: AppConfig) -> checks.Target:
    return checks.Target(root=root, app=app, platform=config.load())


def prepare(ctx: Context, command: str) -> list[str]:
    """Run before every command: inside an app, bring its platform files and guidance to this CLI. Returns hints."""
    if command not in APP_COMMANDS:
        return []
    try:
        root, app = _app(ctx)
    except CliError:
        return []  # The command itself reports a missing or broken app.toml.
    target = _target(root, app)
    step = checks.platform_files(target, "local")
    if step.state == "failed":
        raise CliError(step.detail)
    found = kitfacts.facts(root) if (root / ".venv").is_dir() else None
    if found is not None and found.sbu == app.app.sbu:
        sync.write_build(root, target.platform, app, kitfacts.tokens(found))
    sync.write_guidance(root, {} if found is None else kitfacts.skill_files(found))
    return []


# ---------------------------------------------------------------- setup and login


def setup(_ctx: Context) -> Outcome:
    with auth.http_client() as client:
        results = setup_checks.run(_ctx.cwd, client=client)
    missing = [check for check in results if check.state == "missing"]
    data: dict[str, object] = {"checks": [{"name": check.name, "state": check.state, "detail": check.detail} for check in results]}
    if missing:
        return Outcome(summary="ctzd needs attention: " + ", ".join(check.name for check in missing), data=data, next_steps=[check.detail for check in missing], ok=False)
    data["guide"] = assets.start_guide()
    return Outcome(summary="ctzd is ready.", data=data, next_steps=["Read `guide`: it says how to start a new app or open an existing one."])


def login_start(_ctx: Context, *, open_browser: bool) -> Outcome:
    with auth.http_client() as client:
        pending = auth.start(client=client, now=auth.utc_now, open_browser=open_browser)
    return Outcome(
        summary=f"Ask the person to open {pending.verification_uri} and enter the code {pending.user_code}.",
        data={"user_code": pending.user_code, "verification_uri": pending.verification_uri, "expires_at": pending.expires_at.isoformat()},
        next_steps=["Once they have entered the code and clicked Authorize, run `ctzd login --wait`."],
    )


def login_wait(ctx: Context) -> Outcome:
    with auth.http_client() as client:
        profile = auth.wait(client=client, now=auth.utc_now, sleep=ctx.sleep)
    return Outcome(summary=f"Logged in to GitHub as {profile.login}.", data={"login": profile.login, "email": profile.email})


# ---------------------------------------------------------------- creating apps


def _owner_email(explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    profile = auth.profile()
    if profile is not None and profile.email is not None:
        return profile.email
    msg = "ctzd does not know the owner's email address."
    raise UsageError(msg, hint="Pass --owner EMAIL, or log in with `ctzd login --start` first.")


def _uv(root: Path, *args: str) -> proc.Completed:
    return proc.run([proc.which("uv", hint="Install uv first."), *args], cwd=root, timeout=900)


def new(ctx: Context, *, name: str, sbu: str, kind: Kind, directory: Path | None, owner: str | None, offline: bool) -> Outcome:  # noqa: PLR0913 - one keyword per command-line option.
    if not offline:
        msg = f"Creating the app's GitHub repository {NOT_BUILT}."
        raise UsageError(msg, hint="For platform tests, pass --offline: the app is written locally and can never be deployed.")
    platform = config.load()
    platform.sbu(sbu)
    jobs = (_TEMPLATE_JOB,) if kind == "job" else ()
    app = AppConfig(app=AppSection(name=name, sbu=sbu, kind=kind, owners=(_owner_email(owner),)), sources=SourcesSection(use=("sample",)), jobs=jobs)
    root = (directory or ctx.cwd) / name
    if root.exists() and any(root.iterdir()):
        msg = f"{root} already exists and is not empty."
        raise UsageError(msg, hint="Choose another name or folder.")
    made = not root.exists()
    try:
        root.mkdir(parents=True, exist_ok=True)
        scaffold.scaffold(root, platform=platform, app=app)
        _install(root)
        _write_kit_files(root, platform, app)
    except BaseException:
        _empty(root, remove=made)
        raise
    git = gitops.open_repo(root, token=None, identity=auth.identity() or ("ctzd", "ctzd@localhost"))
    git.init(None)
    git.commit_all(f"Create {name} with ctzd {assets.version()}")
    return Outcome(
        summary=f"Created {name} in {root}, offline: it has no repository and can never be deployed.",
        data={"path": str(root), "repo": None, "url": platform.app_url(sbu, name), "guide": _guide(root)},
        next_steps=[f"Read `guide`, the app's {root / 'AGENTS.md'}, and the skills it names before you write code. Then work inside {root}."],
    )


def _write_kit_files(root: Path, platform: config.PlatformConfig, app: AppConfig) -> None:
    """The files that come from the app's installed kit: `.importlinter`, and the department's skill."""
    found = kitfacts.facts(root, mode="fresh")
    if found is None or found.sbu != app.app.sbu:
        msg = f"The new app's kit is not {platform.sbu(app.app.sbu).kit_package}."
        raise CliError(msg, hint="Check the department's kit in platform.toml, then run the same `ctzd new` command again.")
    sync.write_build(root, platform, app, kitfacts.tokens(found))
    sync.write_guidance(root, kitfacts.skill_files(found))


def _install(root: Path) -> None:
    installed = _uv(root, "sync")
    if not installed.ok:
        msg = "The new app's packages could not be installed."
        raise CliError(msg, hint=proc.tail(installed.output, 20) + "\nFix the cause, then run the same `ctzd new` command again.")


def _guide(root: Path) -> str | None:
    """The app's AGENTS.md, returned by `ctzd new`: the first session starts outside the app folder, where Cursor hides
    the gitignored file from its agent."""
    path = root / "AGENTS.md"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _empty(root: Path, *, remove: bool) -> None:
    """Undo a `ctzd new` that failed: remove the folder it made, or empty the empty one it was given."""
    if remove:
        shutil.rmtree(root, ignore_errors=True)
        return
    for entry in root.iterdir() if root.is_dir() else ():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


# ---------------------------------------------------------------- working locally


def local_env(root: Path, app: AppConfig, *, fake: bool = False) -> dict[str, str]:
    """What the app's code reads of where it runs, on the desktop: the kit serves sample rows for every source with
    `fake`, and for each one `.env` has no credentials for without it."""
    env = {
        "CTZD_APP": app.app.name,
        "CTZD_SBU": app.app.sbu,
        "CTZD_ENV": "local",
        "CTZD_OWNERS": ",".join(app.app.owners),
        "CTZD_STORE_URL": str(root / ".ctzd" / "out" / "store"),
        "CTZD_OUTBOX": str(root / ".ctzd" / "outbox"),
        "CTZD_FIXTURES": str(root / "fixtures"),
    }
    return env | ({"CTZD_FAKE": "1"} if fake else {})


def _uv_run(root: Path, *command: str) -> list[str]:
    uv = proc.which("uv", hint="Install uv first.")
    env_file = ["--env-file", ENV_FILE] if (root / ENV_FILE).is_file() else []
    return [uv, "run", *env_file, *command]


def dev(ctx: Context, *, port: int | None, stop: bool, fake: bool = False) -> Outcome:
    root, app = _app(ctx)
    stopped = devserver.stop(root)
    if stop:
        summary = "The local dashboard stopped." if stopped is not None else "No local dashboard was running."
        return Outcome(summary=summary, data={"stopped": stopped is not None})
    if app.app.kind != "dash":
        msg = "This app has no dashboard; it runs only jobs."
        raise UsageError(msg, hint="Run a job on this desktop with `ctzd run JOB --local`.")
    chosen = _dev_port(port, previous=None if stopped is None else stopped.port)
    command = _uv_run(root, "python", "-m", "app", "--dev", "--port", str(chosen))
    where = " on the sample rows" if fake else ""
    match devserver.start(root, command, port=chosen, env=local_env(root, app, fake=fake)):
        case devserver.Started(server, _process):
            return Outcome(
                summary=f"The dashboard runs at {server.url}{where} in the background; ctzd dev restarts it, ctzd dev --stop stops it.",
                data={"url": server.url, "port": server.port, "log": devserver.LOG.as_posix()},
                next_steps=["Run `ctzd render PAGE` to see what a page shows, as JSON."],
            )
        case devserver.Failed(reason, log):
            return Outcome(summary=f"The local dashboard {reason}.", data={"output": log, "log": devserver.LOG.as_posix()}, ok=False)
        case unreachable:
            assert_never(unreachable)


def _dev_port(asked: int | None, *, previous: int | None) -> int:
    if asked is not None:
        if not devserver.port_free(asked):
            msg = f"Port {asked} is in use by another program."
            raise UsageError(msg, hint="Leave out --port and ctzd dev picks a free one.")
        return asked
    if previous is not None and devserver.port_free(previous):
        return previous
    found = devserver.free_port()
    if found is None:
        msg = f"Ports {devserver.FIRST_PORT} and the next ones are all in use."
        raise CliError(msg, hint="Stop other local dashboards with `ctzd dev --stop` in their app folders.")
    return found


def exec_command(ctx: Context, *, argv: tuple[str, ...]) -> Outcome:
    """Run a command in the app's environment with its credentials, the way `ctzd dev` runs the dashboard."""
    root, app = _app(ctx)
    command = list(argv[1:] if argv[:1] == ("--",) else argv)
    if not command:
        msg = "ctzd exec needs a command after --."
        raise UsageError(msg, hint='For example: ctzd exec --json -- python -c "from app.data import positions; print(positions())"')
    result = proc.run(_uv_run(root, *command), cwd=root, env=local_env(root, app), timeout=3600)
    return Outcome(summary=f"The command {'finished' if result.ok else 'failed'} (exit {result.code}).", data={"exit_code": result.code, "output": proc.tail(result.output, 200)}, ok=result.ok)


def run_job(ctx: Context, *, job: str, local: bool) -> Outcome:
    root, app = _app(ctx)
    app.job(job)
    if not local:
        msg = f"Running a job in an environment's Airflow {NOT_BUILT}."
        raise UsageError(msg, hint=f"Run it on this desktop with `ctzd run {job} --local`.")
    result = proc.run(_uv_run(root, *render.job_command(job)), cwd=root, env=local_env(root, app), timeout=3600)
    return Outcome(summary=f"Job {job} {'finished' if result.ok else 'failed'} locally (exit {result.code}).", data={"exit_code": result.code, "output": proc.tail(result.output, 40)}, ok=result.ok)


def check(ctx: Context, *, fast: bool) -> Outcome:
    root, app = _app(ctx)
    return check_outcome(ctx.run_checks(_target(root, app), mode="local", fast=fast))


def check_outcome(steps: list[checks.Step]) -> Outcome:
    failed = [step for step in steps if step.state == "failed"]
    warnings = [f"{step.name}: {step.warning}" for step in steps if step.warning is not None]
    return Outcome(
        summary="All checks passed." if not failed else "Checks failed: " + ", ".join(step.name for step in failed) + ".",
        data={"steps": [{"name": step.name, "state": step.state, "detail": step.detail} for step in steps]},
        next_steps=(["Fix the failures above, then run `ctzd check` again."] if failed else []) + warnings,
        ok=not failed,
    )


def sync_files(ctx: Context) -> Outcome:
    """Everything `prepare` already did before this command ran: the platform files and guidance are this CLI's."""
    root, _app_config = _app(ctx)
    return Outcome(summary=f"Platform files are ctzd {assets.version()}'s.", data={"version": str(sync.recorded_version(root))})


# ---------------------------------------------------------------- steps the reusable workflow runs


def ci_check(ctx: Context) -> Outcome:
    root, app = _app(ctx)
    return check_outcome(ctx.run_checks(_target(root, app), mode="ci", fast=False))


def ci_render(ctx: Context, *, image: str, release_number: int | None, out: Path) -> Outcome:
    _root, app = _app(ctx)
    platform = config.load()
    out.mkdir(parents=True, exist_ok=True)
    values = render.helm_values(platform, app, image=image, release=release_number)
    spec = render.airflow_spec(platform, app, image=image, release=release_number)
    (out / "helm-values.json").write_text(values.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
    (out / "airflow-spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return Outcome(summary=f"Wrote helm-values.json and airflow-spec.json to {out}.", data={"dashboard": values.enabled, "jobs": len(app.jobs)})


def ci_release(ctx: Context, *, image: str, commit: str, now: Callable[[], object]) -> Outcome:
    root, _app_config = _app(ctx)
    git = gitops.open_repo(root, token=auth.token(), identity=("ctzd", "ctzd@localhost"))
    git.fetch_tags()
    number = release.next_number(release.parse_tags(git.tags(release.TAG_PREFIX)))
    record = release.Release.model_validate({"release": number, "commit": commit, "image": image, "created_at": now(), "app_toml": (root / appconfig.APP_FILE).read_text(encoding="utf-8")})
    name = release.tag_name(number)
    git.tag(name, release.message(record), commit)
    git.push_tag(name)
    return Outcome(summary=f"Tagged {commit[:8]} as {name}.", data={"release": number, "tag": name})


# ---------------------------------------------------------------- looking at data


_TABLES_SHOWN = 40  # a screenful in the summary; `tables` in the JSON has them all


def sources(ctx: Context, *, source: str | None, table: str | None) -> Outcome:
    """The kit's sources and loaders; with SOURCE, a database source's tables; with TABLE, a table's columns and rows."""
    root, app = _app(ctx)
    if source is None:
        found = kitfacts.facts(root)
        if found is None:
            msg = "The app's packages are not installed yet."
            raise CliError(msg, hint="Run `ctzd check`, which installs them, then `ctzd sources` again.")
        lines = [f"{found.title}'s sources (kit {found.kit}):"]
        for item in found.sources:
            used = " [in app.toml]" if item.name in app.sources.use else ""
            sample = f" Serves sample rows here: {item.serves_sample}." if item.serves_sample is not None else ""
            lines.append(f"- {item.name} ({item.kind}){used}: {item.description}{sample}")
            lines += [f"    {loader.example}" for loader in item.loaders]
        return Outcome(
            summary="\n".join(lines),
            data={"kit": found.kit, "sources": [item.model_dump() for item in found.sources]},
            next_steps=[
                "Pick the source whose description matches what the person asked for, and tell them in business words which one you will use.",
                "Add it to [sources].use in app.toml, call its loaders from src/app/data/, and see the `sbu-" + found.sbu + "` skill. For a database source, `ctzd sources SOURCE` lists its tables.",
            ],
        )
    args = ("tables", source) if table is None else ("table", source, table)
    found_tables = kitfacts.run_explore(root, *args, env=local_env(root, app))
    listed = found_tables.get("tables")
    if isinstance(listed, list):
        names = [".".join(str(part) for part in (item.get("schema_name"), item.get("name")) if part) for item in listed if isinstance(item, dict)]
        more = f" and {len(names) - _TABLES_SHOWN} more" if len(names) > _TABLES_SHOWN else ""
        return Outcome(
            summary=f"{source} has {len(names)} table(s) and view(s): {', '.join(names[:_TABLES_SHOWN])}{more}.",
            data=found_tables,
            next_steps=[f"Run `ctzd sources {source} TABLE` for a table's columns and first rows."],
        )
    if "columns" in found_tables:
        return Outcome(
            summary=f"{table} in {source}: its columns and first rows are below.",
            data=found_tables,
            next_steps=[f"Select only the columns the page needs, ids as text, in src/app/data/sql/; write made-up rows for it in fixtures/{source}/FILE.csv (never these real rows)."],
        )
    return Outcome(summary=str(found_tables.get("message", f"{source} has no tables.")), data=found_tables)


# ---------------------------------------------------------------- kits


def kit_new(ctx: Context, *, sbu: str, title: str, directory: Path | None) -> Outcome:
    kit, written = kitwork.new(directory or ctx.cwd, sbu=sbu, title=title)
    return Outcome(
        summary=f"Started the {title} kit in {kit.root}, with only the sample source.",
        data={"path": str(kit.root), "module": kit.module, "files": written},
        next_steps=[
            f"List the department in packages/ctzd-cli/src/ctzd_cli/platform.toml's [sbus.{sbu}], run `uv sync`, then add its first source with `ctzd kit add source NAME --kind KIND` in {kit.root}.",
            "Read the ctzd-kits skill: it says how to wrap a department package and write sample rows.",
        ],
    )


def kit_add_source(ctx: Context, *, name: str, kind: str, directory: Path | None) -> Outcome:
    kit = kitwork.find_kit(directory or ctx.cwd)
    written = kitwork.add_source(kit, name=name, kind=kind)
    return Outcome(
        summary=f"Added the {kind} source {name} to {kit.module}, with a working loader, sample rows and a test.",
        data={"files": written},
        next_steps=[
            f"Say what {name} holds and when it is current in its source.toml, rename and describe its loader, and write a few made-up rows for each loader in its fixtures/.",
            f"Then run `ctzd kit check {kit.root}`.",
        ],
    )


def kit_check(ctx: Context, *, directory: Path | None, make_apps: bool) -> Outcome:
    checked = kitwork.check(directory or ctx.cwd, make_apps=make_apps)
    outcome = check_outcome(checked.steps)
    outcome.summary = outcome.summary.replace("All checks passed.", "The kit meets the contract.")
    outcome.next_steps += [f"Warning: {warning}" for warning in checked.warnings]
    return outcome
