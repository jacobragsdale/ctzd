"""The ctzd commands. Each returns an Outcome; `main` prints it as text or JSON."""

import contextlib
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, assert_never

from pydantic import ValidationError

from ctzd_cli import api_client, appconfig, assets, auth, checks, config, devserver, gitops, proc, release, render, scaffold, setup_checks, sync
from ctzd_cli import api_models as m
from ctzd_cli import shot as shooting
from ctzd_cli.appconfig import AppConfig, AppSection, Job, SourcesSection, Ui
from ctzd_cli.assets import start_guide
from ctzd_cli.errors import CliError, NotFoundError, UsageError
from ctzd_cli.releases import ReleaseSource, require_usable, upgrade_hint

ENV_FILE = ".env"
_POLL_SECONDS = 10.0
_DEFAULT_JOB = Job(name="refresh", schedule="0 6 * * *")
# Commands that work inside an app; each refreshes the app's guidance before it runs.
APP_COMMANDS = frozenset({"dev", "run", "exec", "add job", "check", "env", "deploy", "status", "logs", "releases", "rollback", "sync", "delete"})


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
    """What every command needs: where it runs, and how to reach GitHub and the platform."""

    cwd: Path
    platform_factory: Callable[[], api_client.Platform]
    token: Callable[[], str] = auth.require_token
    run_checks: CheckRunner = checks.run_all
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = auth.utc_now
    releases: ReleaseSource = field(default_factory=lambda: ReleaseSource(None))


def default_platform() -> api_client.Platform:
    return api_client.PlatformClient(config.load().platform.api_url, auth.require_token())


# ---------------------------------------------------------------- guidance


def refresh_guidance(ctx: Context, root: Path, app: AppConfig, *, force: bool = False) -> list[str]:
    """Bring AGENTS.md and the skills to the channel's current release; return hints for the agent."""
    stamp = sync.read_stamp(root)
    now = ctx.now()
    if not force and stamp is not None and now - stamp.checked_at < sync.GUIDANCE_REFRESH and (root / "AGENTS.md").is_file():
        return []
    current = ctx.releases.current(app.app.sbu, app.app.name)
    hints: list[str] = []
    if ctx.releases.unreachable is not None:
        hints.append(f"The agent guidance could not be refreshed ({ctx.releases.unreachable}); it may be out of date.")
    if current is not None:
        chosen = require_usable(ctx.releases.get(current.release))
        hint = upgrade_hint(current)
        if hint is not None:
            hints.append(hint)
    elif not (root / "AGENTS.md").is_file():
        chosen = require_usable(ctx.releases.newest_local())
    else:
        # Without the platform, keep the guidance that is there and ask again on the next command.
        return hints
    sync.write_guidance(root, chosen, app.app.sbu)
    sync.write_stamp(root, chosen.release, now)
    return hints


def prepare(ctx: Context, command: str) -> list[str]:
    """Run before every command: inside an app, refresh its guidance. Returns hints to add to `next`."""
    if command not in APP_COMMANDS:
        return []
    try:
        root = appconfig.find_root(ctx.cwd)
        app = appconfig.load(root)
    except CliError:
        return []  # The command itself reports a missing or broken app.toml.
    return refresh_guidance(ctx, root, app)


# ---------------------------------------------------------------- setup and login


def setup(ctx: Context) -> Outcome:
    with auth.http_client() as client:
        results = setup_checks.run(ctx.cwd, client=client)
    missing = [check for check in results if check.state == "missing"]
    data: dict[str, object] = {"checks": [{"name": check.name, "state": check.state, "detail": check.detail} for check in results]}
    if missing:
        return Outcome(summary="ctzd needs attention: " + ", ".join(check.name for check in missing), data=data, next_steps=[check.detail for check in missing], ok=False)
    # The guide comes from the platform's current release, so starting an app is taught by served text.
    data["guide"] = start_guide(require_usable(ctx.releases.for_app()))
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


# ---------------------------------------------------------------- creating and opening apps


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


def new(ctx: Context, *, name: str, sbu: str, ui: Ui, directory: Path | None, owner: str | None, offline: bool) -> Outcome:  # noqa: PLR0913 - one keyword per command-line option.
    platform = config.load()
    platform.sbu(sbu)
    app = AppConfig(app=AppSection(name=name, sbu=sbu, ui=ui, owners=(_owner_email(owner),)), sources=SourcesSection(), jobs=(_DEFAULT_JOB,) if ui == "none" else ())
    root = (directory or ctx.cwd) / name
    if root.exists() and any(root.iterdir()):
        msg = f"{root} already exists and is not empty."
        raise UsageError(msg, hint="Choose another name or folder.")
    created = None if offline else ctx.platform_factory().create_app(m.CreateAppRequest(sbu=sbu, name=name, ui=ui, owners=app.app.owners))
    chosen = require_usable(ctx.releases.newest_local() if offline else ctx.releases.for_app(sbu, name))
    root.mkdir(parents=True, exist_ok=True)
    scaffold.scaffold(root, platform=platform, app=app, release=chosen)
    sync.write_stamp(root, chosen.release, ctx.now())
    locked = _uv(root, "sync")
    if not locked.ok:
        msg = "The new app's packages could not be installed."
        raise CliError(msg, hint=proc.tail(locked.output, 20))
    if ui == "react":
        for step in (checks.web_install(root, "local"), checks.client(root, "local")):
            if step.state == "failed":
                msg = f"The new app's pages could not be set up ({step.name})."
                raise CliError(msg, hint=step.detail)
    git = gitops.open_repo(root, token=None if offline else ctx.token(), identity=auth.identity() or ("ctzd", "ctzd@localhost"))
    git.init(None if created is None else created.clone_url)
    git.commit_all(f"Create {name} on platform release {chosen.release}")
    if created is not None:
        git.push_main()
    url = platform.app_url(sbu, name)
    return Outcome(
        summary=f"Created {name} in {root}." + ("" if created is None else f" It will be live at {url} in a few minutes."),
        data={"path": str(root), "repo": None if created is None else created.repo, "url": url},
        next_steps=[f"Work inside {root}. Read {root / 'AGENTS.md'} and the skills it names before you write code.", "Run `ctzd dev` to see it locally, then `ctzd deploy` to ship changes."],
    )


def open_app(ctx: Context, *, name: str, sbu: str, directory: Path | None) -> Outcome:
    platform = config.load()
    root = (directory or ctx.cwd) / name
    if root.exists():
        msg = f"{root} already exists."
        raise UsageError(msg, hint="Choose another folder with --dir.")
    try:
        ctx.platform_factory().status(sbu, name)
    except NotFoundError:
        msg = f"There is no app {platform.repo_name(sbu, name)} on the platform."
        raise UsageError(msg, hint=f"Check the name and business unit with the person; a link such as {platform.app_url(sbu, name)} holds both.") from None
    url = f"https://{platform.github.host}/{platform.github.org}/{platform.repo_name(sbu, name)}.git"
    gitops.open_repo(ctx.cwd, token=ctx.token(), identity=auth.identity()).clone(url, root)
    hints = refresh_guidance(ctx, root, appconfig.load(root), force=True)
    installed = _uv(root, "sync")
    return Outcome(
        summary=f"Opened {name} in {root}.",
        data={"path": str(root), "url": platform.app_url(sbu, name)},
        next_steps=[f"Work inside {root}. Read {root / 'AGENTS.md'} before you change anything: it says what to do first when the live app is wrong.", *hints]
        + ([] if installed.ok else ["`uv sync` failed; run `ctzd check` to see why."]),
    )


# ---------------------------------------------------------------- working locally


def _target(ctx: Context, root: Path, app: AppConfig) -> checks.Target:
    return checks.Target(root=root, app=app, platform=config.load(), releases=ctx.releases)


def _app(ctx: Context) -> tuple[Path, AppConfig]:
    root = appconfig.find_root(ctx.cwd)
    return root, appconfig.load(root)


def _platform_app(ctx: Context) -> tuple[Path, AppConfig]:
    """The app, for a command that needs it on the platform: one made with `ctzd new --offline` is not."""
    root, app = _app(ctx)
    if not (root / ".git").exists() or not gitops.open_repo(root, token=None, identity=None).has_remote():
        msg = f"{app.app.name} is not on the platform: it has no repository, as when it was made with `ctzd new --offline`."
        hint = f"Run `ctzd new {app.app.name} --sbu {app.app.sbu} --dir PARENT` in another folder, copy this app's src/, tests/, app.toml, and README.md into it, and work there."
        raise UsageError(msg, hint=hint)
    return root, app


def local_env(root: Path, app: AppConfig) -> dict[str, str]:
    env = {"CTZD_APP": app.app.name, "CTZD_SBU": app.app.sbu, "CTZD_ENV": "local", "CTZD_STORE_URL": str(root / ".ctzd" / "out" / "store")}
    if _env_file_has(root, "CTZD_SHARED_STORE_SAS"):
        env["CTZD_SHARED_STORE_URL"] = config.load().platform.store_url
    return env


def _env_file_has(root: Path, key: str) -> bool:
    path = root / ENV_FILE
    if not path.is_file():
        return False
    return any(line.split("=", 1)[0].strip() == key and line.split("=", 1)[1].strip() not in {"", "''", '""'} for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def _uv_run(root: Path, *command: str) -> list[str]:
    uv = proc.which("uv", hint="Install uv first.")
    env_file = ["--env-file", ENV_FILE] if (root / ENV_FILE).is_file() else []
    return [uv, "run", *env_file, *command]


def dev(ctx: Context, *, port: int | None, stop: bool) -> Outcome:
    root, app = _app(ctx)
    stopped = devserver.stop(root)
    if stop:
        summary = "The local dashboard stopped." if stopped is not None else "No local dashboard was running."
        return Outcome(summary=summary, data={"stopped": stopped is not None})
    if app.app.ui == "none":
        msg = "This app has no dashboard."
        raise UsageError(msg, hint="Run a job locally with `ctzd run JOB --local`.")
    chosen = _dev_port(port, previous=None if stopped is None else stopped.port)
    for step in (checks.web_install(root, "local"), checks.client(root, "local"), checks.web_build(root)):
        if step.state == "failed":
            return Outcome(summary=f"The page could not be built ({step.name}).", data={"step": step.name, "output": step.detail}, ok=False)
    command = _uv_run(root, "uvicorn", "--factory", "ctzd.api:asgi", "--host", "localhost", "--port", str(chosen))
    match devserver.start(root, command, port=chosen, env=local_env(root, app)):
        case devserver.Started(server, _process):
            return Outcome(
                summary=f"The dashboard runs at {server.url} in the background; ctzd dev restarts it, ctzd dev --stop stops it.",
                data={"url": server.url, "port": server.port, "log": devserver.LOG.as_posix()},
            )
        case devserver.Failed(reason, log):
            return Outcome(summary=f"The local dashboard {reason}.", data={"output": log, "log": devserver.LOG.as_posix()}, ok=False)
        case unreachable:
            assert_never(unreachable)


def shot(ctx: Context, *, page: str) -> Outcome:
    """A picture of a page that `ctzd dev` serves, and each problem on it, for the agent to look at."""
    root, app = _app(ctx)
    if app.app.ui != "react":
        msg = "ctzd shot takes pictures of a React app's pages."
        raise UsageError(msg)
    server = devserver.recorded(root)
    if server is None:
        msg = "No local dashboard is running."
        raise UsageError(msg, hint="Run `ctzd dev`, then `ctzd shot` again.")
    path = page if page.startswith("/") else f"/{page}"
    result = shooting.shoot(server.url + path, shooting.picture_path(root, path))
    picture = result.picture.relative_to(root).as_posix() if result.picture.is_file() else None
    look = f"Look at {picture} before you show the person the page." if picture is not None else "Fix the problems, run `ctzd dev`, then `ctzd shot` again."
    if result.ok:
        return Outcome(summary=f"The page {path} drew without problems.", data={"url": result.url, "picture": picture, "problems": []}, next_steps=[look])
    return Outcome(
        summary=f"The page {path} has {len(result.problems)} problem(s): " + "; ".join(result.problems),
        data={"url": result.url, "picture": picture, "problems": result.problems},
        next_steps=["Fix each problem, run `ctzd dev`, then `ctzd shot` again.", look],
        ok=False,
    )


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
    """Run a command in the app's environment with its source credentials, the way `ctzd dev` runs the page."""
    root, app = _app(ctx)
    command = list(argv[1:] if argv[:1] == ("--",) else argv)
    if not command:
        msg = "ctzd exec needs a command after --."
        raise UsageError(msg, hint='For example: ctzd exec --json -- python -c "from app.data.loaders import load_prices; print(load_prices().head())"')
    result = proc.run(_uv_run(root, *command), cwd=root, env=local_env(root, app), timeout=3600)
    return Outcome(summary=f"The command {'finished' if result.ok else 'failed'} (exit {result.code}).", data={"exit_code": result.code, "output": proc.tail(result.output, 200)}, ok=result.ok)


def run_job(ctx: Context, *, job: str, local: bool, wait: bool, timeout: float) -> Outcome:
    root, app = _app(ctx)
    app.job(job)
    if local:
        result = proc.run(_uv_run(root, *render.job_command(job)), cwd=root, env=local_env(root, app), timeout=3600)
        return Outcome(
            summary=f"Job {job} {'finished' if result.ok else 'failed'} locally (exit {result.code}).", data={"exit_code": result.code, "output": proc.tail(result.output, 40)}, ok=result.ok
        )
    _platform_app(ctx)
    platform = ctx.platform_factory()
    created = platform.run_job(app.app.sbu, app.app.name, job)
    if not wait:
        return Outcome(summary=f"Started job {job} in Airflow.", data=created.model_dump(), next_steps=[f"Follow the run at {created.url}, or run `ctzd run {job} --wait`."])
    deadline = ctx.clock() + timeout
    state = platform.job_run(app.app.sbu, app.app.name, job, created.run_id)
    while state.state in {"queued", "running"} and ctx.clock() < deadline:
        ctx.sleep(_POLL_SECONDS)
        state = platform.job_run(app.app.sbu, app.app.name, job, created.run_id)
    if state.state == "failed":
        return Outcome(summary=f"Job {job} failed in Airflow.", data=state.model_dump(), next_steps=[f"Read `ctzd logs --job {job}`, fix the cause, deploy, and run it again."], ok=False)
    finished = state.state == "success"
    return Outcome(
        summary=f"Job {job} {'finished' if finished else 'is still ' + state.state} in Airflow.", data=state.model_dump(), next_steps=[] if finished else [f"Follow the run at {state.url}."]
    )


def sources(ctx: Context) -> Outcome:
    found = ctx.platform_factory().sources().sources
    return Outcome(
        summary="Sources: " + ", ".join(found) if found else "No sources are configured yet.",
        data={"sources": list(found)},
        next_steps=["Add the ones the app reads to [sources].use in app.toml, then run `ctzd env`."],
    )


def add_job(ctx: Context, *, name: str, schedule: str, size: appconfig.Size, timezone: str = "UTC") -> Outcome:
    root, _app_config = _app(ctx)
    try:
        job = Job(name=name, schedule=schedule, size=size, timezone=timezone)
    except ValidationError as err:
        problems = "; ".join(str(error["msg"]).removeprefix("Value error, ") for error in err.errors(include_url=False))
        raise UsageError(problems) from None
    recorded = sync.recorded_release(root)
    chosen = ctx.releases.newest_local() if recorded is None else ctx.releases.get(recorded)
    appconfig.add_job(root, job)
    files = scaffold.add_job_files(root, require_usable(chosen), job)
    return Outcome(
        summary=f"Added job {name} on schedule {schedule!r} ({timezone}).",
        data={"job": name, "files": files},
        next_steps=[f"Write the job in src/app/jobs/{name}.py, then run `ctzd run {name} --local`."],
    )


def check(ctx: Context, *, fast: bool) -> Outcome:
    root, app = _app(ctx)
    steps = ctx.run_checks(_target(ctx, root, app), mode="local", fast=fast)
    return _check_outcome(steps)


def _check_outcome(steps: list[checks.Step]) -> Outcome:
    failed = [step for step in steps if step.state == "failed"]
    return Outcome(
        summary="All checks passed." if not failed else "Checks failed: " + ", ".join(step.name for step in failed) + ".",
        data={"steps": [{"name": step.name, "state": step.state, "detail": step.detail} for step in steps]},
        next_steps=["Fix the failures above, then run `ctzd check` again."] if failed else [],
        ok=not failed,
    )


def _dotenv_value(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def env(ctx: Context) -> Outcome:
    root, app = _platform_app(ctx)
    variables = ctx.platform_factory().env(app.app.sbu, app.app.name, app.sources.use).variables
    lines = ["# Written by `ctzd env`. Read-only credentials: never open, print, or commit this file."]
    lines += [f"{key}={_dotenv_value(value)}" for key, value in sorted(variables.items())]
    path = root / ENV_FILE
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Outcome(summary=f"Wrote {len(variables)} credential(s) to .env.", data={"variables": sorted(variables)})


# ---------------------------------------------------------------- shipping


def wait_for_build(ctx: Context, platform: api_client.Platform, app: AppConfig, commit: str, *, timeout: float) -> m.Build:
    deadline = ctx.clock() + timeout
    build = platform.build_for_commit(app.app.sbu, app.app.name, commit)
    while build.state in {"queued", "running", "not_found"} and ctx.clock() < deadline:
        ctx.sleep(_POLL_SECONDS)
        build = platform.build_for_commit(app.app.sbu, app.app.name, commit)
    return build


def _build_outcome(platform: api_client.Platform, app: AppConfig, commit: str, build: m.Build) -> Outcome:
    data: dict[str, object] = {"commit": commit, "build": build.model_dump()}
    if build.state == "failed":
        return Outcome(
            summary=f"The deploy failed at {build.stage or 'an unknown step'}. Production still runs the previous release.",
            data=data,
            next_steps=["Read build.log_tail, fix the cause, then run `ctzd deploy` again.", "See the ctzd-troubleshoot skill."],
            ok=False,
        )
    if build.state != "succeeded":
        return Outcome(summary=f"The build is still {build.state}.", data=data, next_steps=["Run `ctzd status --wait` to keep waiting."])
    if build.release is None:
        # Jenkins passes the checks but skips the deploy while the app is paused after a rollback.
        return Outcome(
            summary="The build passed its checks but nothing was deployed; production still runs the previous release.",
            data=data,
            next_steps=["Run `ctzd status`: if auto-deploy is still paused after a rollback, run `ctzd deploy` again, which clears the pause before it pushes."],
            ok=False,
        )
    status = platform.status(app.app.sbu, app.app.name)
    data |= {"release": build.release, "url": status.url if app.app.ui != "none" else None, "dags": status.dags}
    where = status.url if app.app.ui != "none" else ", ".join(status.dags.values()) or "Airflow"
    return Outcome(summary=f"Release v{build.release} is live: {where}", data=data)


_NAMES_IN_MESSAGE = 5


def commit_message(changed: list[str], message: str | None) -> str:
    if message is not None:
        return message
    if not changed:
        return "Resume auto-deploy after a rollback"
    more = " and more" if len(changed) > _NAMES_IN_MESSAGE else ""
    return "Update " + ", ".join(changed[:_NAMES_IN_MESSAGE]) + more


def _outside_managed(text: str | None) -> object:
    """pyproject.toml without the tables the platform manages: what the person may change."""
    if text is None:
        return None
    try:
        document: dict[str, object] = dict(tomllib.loads(text))
    except tomllib.TOMLDecodeError:
        return None
    for dotted in sync.MANAGED_PYPROJECT_KEYS:
        *parents, last = dotted.split(".")
        node: object = document
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(last, None)
    return document


def platform_changes(ctx: Context, root: Path, git: gitops.Git) -> list[str]:
    """Uncommitted changes that are the platform's: build files of the recorded release or of HEAD's.

    pyproject.toml and uv.lock count as the platform's only while the person
    has changed nothing in pyproject.toml outside the managed tables.
    """
    changed = git.changed_paths()
    owned = {sync.VERSION_FILE}
    committed = git.show(sync.VERSION_FILE)
    numbers = {sync.recorded_release(root), int(committed) if committed is not None and committed.strip().isdigit() else None}
    for number in sorted(number for number in numbers if number is not None):
        with contextlib.suppress(CliError):
            owned |= assets.locked_files(ctx.releases.get(number), appconfig.load(root).app.ui).keys()
    paths = [path for path in changed if path in owned]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8") if (root / "pyproject.toml").is_file() else None
    if _outside_managed(git.show("pyproject.toml")) == _outside_managed(pyproject):
        paths += [path for path in changed if path in {"pyproject.toml", "uv.lock"}]
    return sorted(paths)


def platform_commit_message(root: Path, paths: list[str]) -> str:
    recorded = sync.recorded_release(root)
    verb = "Upgrade" if sync.VERSION_FILE in paths else "Restore"
    return f"{verb} platform files to platform release {recorded}"


def deploy(ctx: Context, *, message: str | None, detach: bool, timeout: float) -> Outcome:
    root, app = _platform_app(ctx)
    steps = ctx.run_checks(_target(ctx, root, app), mode="local", fast=False)
    gate = _check_outcome(steps)
    if not gate.ok:
        gate.summary = "Not deployed. " + gate.summary
        return gate
    git = gitops.open_repo(root, token=ctx.token(), identity=auth.identity() or ("ctzd", "ctzd@localhost"))
    platform = ctx.platform_factory()
    # The platform's own changes go in a commit of their own, before the person's change.
    platform_paths = platform_changes(ctx, root, git)
    upgraded = git.commit_paths(platform_paths, platform_commit_message(root, platform_paths))
    changed = git.changed_paths()
    paused = platform.status(app.app.sbu, app.app.name).paused
    # Clear a rollback pause before pushing, so the build this push starts deploys.
    platform.resume(app.app.sbu, app.app.name)
    # With nothing new, a deploy still needs a new build when the last one failed (a passing hiccup) or after a rollback.
    head = git.head()
    retry = not changed and not upgraded and not paused and head is not None and platform.build_for_commit(app.app.sbu, app.app.name, head).state == "failed"
    summary = (message or "Deploy again after a failed build") if retry else commit_message(changed, message)
    committed = git.commit_all(summary, allow_empty=(paused or retry) and not upgraded) or upgraded
    git.rebase_onto_remote()
    git.push_main()
    commit = git.head() or ""
    if detach:
        return Outcome(summary="Pushed; Jenkins is building it.", data={"commit": commit, "committed": committed}, next_steps=["Run `ctzd status --wait`."])
    build = wait_for_build(ctx, platform, app, commit, timeout=timeout)
    return _build_outcome(platform, app, commit, build)


def status(ctx: Context, *, wait: bool, timeout: float) -> Outcome:
    root, app = _platform_app(ctx)
    platform = ctx.platform_factory()
    if wait:
        commit = gitops.open_repo(root, token=None, identity=None).head()
        if commit is None:
            msg = "This app has no commits yet."
            raise UsageError(msg)
        return _build_outcome(platform, app, commit, wait_for_build(ctx, platform, app, commit, timeout=timeout))
    current = platform.status(app.app.sbu, app.app.name)
    paused = " Auto-deploy is paused after a rollback; the next `ctzd deploy` resumes it." if current.paused else ""
    release_text = f"v{current.release}" if current.release is not None else "nothing yet"
    return Outcome(summary=f"{app.app.name} runs {release_text}.{paused}", data=current.model_dump())


def logs(ctx: Context, *, job: str | None, tail: int) -> Outcome:
    _root, app = _platform_app(ctx)
    if job is not None:
        app.job(job)
    text = ctx.platform_factory().logs(app.app.sbu, app.app.name, job=job, tail=tail).text
    return Outcome(summary=text or "No log lines yet.", data={"text": text})


def list_releases(ctx: Context) -> Outcome:
    root, _app_config = _platform_app(ctx)
    git = gitops.open_repo(root, token=ctx.token(), identity=None)
    git.fetch_tags()
    found = release.parse_tags(git.tags(release.TAG_PREFIX))
    lines = [f"v{item.release}  {item.created_at:%Y-%m-%d %H:%M} UTC  {item.commit[:8]}" for item in found]
    return Outcome(summary="\n".join(lines) or "No releases yet.", data={"releases": [item.model_dump(mode="json", exclude={"app_toml"}) for item in found]})


def parse_release(text: str | None) -> int | None:
    if text is None:
        return None
    digits = text.removeprefix("v")
    if not digits.isdigit():
        msg = f"{text!r} is not a release; use the form v12."
        raise UsageError(msg)
    return int(digits)


def rollback(ctx: Context, *, target: str | None, timeout: float) -> Outcome:
    _root, app = _platform_app(ctx)
    platform = ctx.platform_factory()
    accepted = platform.rollback(app.app.sbu, app.app.name, parse_release(target))
    deadline = ctx.clock() + timeout
    current = platform.status(app.app.sbu, app.app.name)
    while current.release != accepted.release and ctx.clock() < deadline:
        ctx.sleep(_POLL_SECONDS)
        current = platform.status(app.app.sbu, app.app.name)
    done = current.release == accepted.release
    return Outcome(
        summary=(f"Rolled back to v{accepted.release}." if done else f"Rolling back to v{accepted.release}; not finished yet.") + " Auto-deploy is paused until the next `ctzd deploy`.",
        data={"release": accepted.release, "done": done, "url": current.url},
        next_steps=["Fix the problem (or `git revert` the bad change), then run `ctzd deploy`."],
        ok=True,
    )


def sync_files(ctx: Context) -> Outcome:
    root, app = _app(ctx)
    step = checks.platform_files(_target(ctx, root, app), "local")
    if step.state == "failed":
        raise CliError(step.detail)
    refresh_guidance(ctx, root, app, force=True)
    recorded = sync.recorded_release(root)
    return Outcome(summary=f"Platform files are at platform release {recorded}." + (f" {step.detail}" if step.detail else ""), data={"release": recorded, "changed": list(step.paths)})


def delete(ctx: Context, *, confirmed: bool) -> Outcome:
    _root, app = _app(ctx)
    if not confirmed:
        msg = "Deleting an app removes its dashboard and jobs; its tables go after 30 days."
        raise UsageError(msg, hint="Confirm with the person, then run `ctzd delete --yes`.")
    _platform_app(ctx)
    ctx.platform_factory().delete(app.app.sbu, app.app.name)
    return Outcome(summary=f"Deleted {app.app.name}. Its tables are removed in 30 days.")


# ---------------------------------------------------------------- steps Jenkins runs


def ci_check(ctx: Context) -> Outcome:
    root, app = _app(ctx)
    return _check_outcome(ctx.run_checks(_target(ctx, root, app), mode="ci", fast=False))


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


def ci_smoke(ctx: Context, *, url: str) -> Outcome:
    _root, app = _app(ctx)
    from ctzd_cli import smoke  # noqa: PLC0415 - playwright comes with the optional ci extra, so import it only here.

    result = smoke.smoke(url, ui=app.app.ui)
    return Outcome(
        summary="The dashboard rendered without errors." if result.ok else "The dashboard shows errors: " + "; ".join(result.problems),
        data={"pages": result.pages, "problems": result.problems},
        ok=result.ok,
    )
