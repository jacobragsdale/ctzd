"""The ctzd commands. Each returns an Outcome; `main` prints it as text or JSON."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ctzd_cli import api_client, appconfig, assets, auth, checks, config, gitops, proc, release, render, scaffold, setup_checks, sync
from ctzd_cli import api_models as m
from ctzd_cli.appconfig import AppConfig, AppSection, Job, SourcesSection, Ui
from ctzd_cli.errors import CliError, UsageError

ENV_FILE = ".env"
SCREENSHOTS = ".ctzd/screenshots"
_POLL_SECONDS = 10.0
_DEFAULT_JOB = Job(name="refresh", schedule="0 6 * * *")


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
    run_checks: "CheckRunner" = checks.run_all
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def default_platform() -> api_client.Platform:
    return api_client.PlatformClient(config.load().platform.api_url, auth.require_token())


# ---------------------------------------------------------------- setup and login


def setup(ctx: Context) -> Outcome:
    with auth.http_client() as client:
        results = setup_checks.run(ctx.cwd, client=client)
    missing = [check for check in results if check.state == "missing"]
    next_steps = [check.detail for check in missing] or ["Ask the person what they want to build, then run `ctzd new NAME --sbu SBU --ui streamlit`."]
    return Outcome(
        summary="ctzd is ready." if not missing else "ctzd needs attention: " + ", ".join(check.name for check in missing),
        data={"checks": [{"name": check.name, "state": check.state, "detail": check.detail} for check in results]},
        next_steps=next_steps,
        ok=not missing,
    )


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
    root.mkdir(parents=True, exist_ok=True)
    scaffold.scaffold(root, platform=platform, app=app, version=assets.version())
    locked = _uv(root, "sync")
    if not locked.ok:
        msg = "The new app's packages could not be installed."
        raise CliError(msg, hint=proc.tail(locked.output, 20))
    git = gitops.open_repo(root, token=None if offline else ctx.token(), identity=auth.identity() or ("ctzd", "ctzd@localhost"))
    git.init(None if created is None else created.clone_url)
    git.commit_all(f"Create {name} with ctzd {assets.version()}")
    if created is not None:
        git.push_main()
    url = platform.app_url(sbu, name)
    return Outcome(
        summary=f"Created {name} in {root}." + ("" if created is None else f" It will be live at {url} in a few minutes."),
        data={"path": str(root), "repo": None if created is None else created.repo, "url": url},
        next_steps=[f"Work inside {root}.", "Run `ctzd dev` to see it locally, then `ctzd deploy` to ship changes."],
    )


def open_app(ctx: Context, *, name: str, sbu: str, directory: Path | None) -> Outcome:
    platform = config.load()
    root = (directory or ctx.cwd) / name
    if root.exists():
        msg = f"{root} already exists."
        raise UsageError(msg, hint="Choose another folder with --dir.")
    url = f"https://{platform.github.host}/{platform.github.org}/{platform.repo_name(sbu, name)}.git"
    gitops.open_repo(ctx.cwd, token=ctx.token(), identity=auth.identity()).clone(url, root)
    sync.mirror_skills_for_claude(root)
    installed = _uv(root, "sync")
    return Outcome(
        summary=f"Opened {name} in {root}.",
        data={"path": str(root), "url": platform.app_url(sbu, name)},
        next_steps=[f"Work inside {root}."] + ([] if installed.ok else ["`uv sync` failed; run `ctzd check` to see why."]),
    )


# ---------------------------------------------------------------- working locally


def _target(root: Path, app: AppConfig) -> checks.Target:
    return checks.Target(root=root, app=app, platform=config.load(), version=assets.version())


def _app(ctx: Context) -> tuple[Path, AppConfig]:
    root = appconfig.find_root(ctx.cwd)
    return root, appconfig.load(root)


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


def dev(ctx: Context, *, port: int, run: Callable[[list[str], Path, dict[str, str]], int]) -> Outcome:
    root, app = _app(ctx)
    if app.app.ui == "none":
        msg = "This app has no dashboard."
        raise UsageError(msg, hint="Run a job locally with `ctzd run JOB --local`.")
    if app.app.ui == "streamlit":
        command = _uv_run(root, "streamlit", "run", "src/app/ui/main.py", "--server.port", str(port), "--server.headless", "true")
    else:
        command = _uv_run(root, "python", "-c", f"from app.ui.main import app; app.run(debug=True, port={port})")
    code = run(command, root, local_env(root, app))
    return Outcome(summary=f"The local dashboard stopped (exit {code}).", data={"url": f"http://localhost:{port}", "exit_code": code}, ok=code == 0)


def run_job(ctx: Context, *, job: str, local: bool) -> Outcome:
    root, app = _app(ctx)
    app.job(job)
    if local:
        result = proc.run(_uv_run(root, "python", "-m", f"app.jobs.{job}"), cwd=root, env=local_env(root, app), timeout=3600)
        return Outcome(
            summary=f"Job {job} {'finished' if result.ok else 'failed'} locally (exit {result.code}).", data={"exit_code": result.code, "output": proc.tail(result.output, 40)}, ok=result.ok
        )
    created = ctx.platform_factory().run_job(app.app.sbu, app.app.name, job)
    return Outcome(summary=f"Started job {job} in Airflow.", data=created.model_dump(), next_steps=[f"Follow the run at {created.url}."])


def add_job(ctx: Context, *, name: str, schedule: str, size: appconfig.Size) -> Outcome:
    root, _app_config = _app(ctx)
    try:
        job = Job(name=name, schedule=schedule, size=size)
    except ValueError as err:
        raise UsageError(str(err)) from None
    appconfig.add_job(root, job)
    files = scaffold.add_job_files(root, job)
    return Outcome(
        summary=f"Added job {name} on schedule {schedule!r} (UTC).", data={"job": name, "files": files}, next_steps=[f"Write the job in src/app/jobs/{name}.py, then run `ctzd run {name} --local`."]
    )


def check(ctx: Context, *, fast: bool) -> Outcome:
    root, app = _app(ctx)
    steps = ctx.run_checks(_target(root, app), mode="local", fast=fast)
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
    root, app = _app(ctx)
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


def _build_outcome(root: Path, platform: api_client.Platform, app: AppConfig, commit: str, build: m.Build) -> Outcome:
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
    status = platform.status(app.app.sbu, app.app.name)
    data |= {"release": build.release, "url": status.url if app.app.ui != "none" else None, "dags": status.dags}
    if build.has_screenshot and build.number is not None:
        shots = root / SCREENSHOTS
        shots.mkdir(parents=True, exist_ok=True)
        shot = shots / f"v{build.release}.png"
        shot.write_bytes(platform.screenshot(app.app.sbu, app.app.name, build.number))
        data["screenshot"] = str(shot)
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


def deploy(ctx: Context, *, message: str | None, detach: bool, timeout: float) -> Outcome:
    root, app = _app(ctx)
    steps = ctx.run_checks(_target(root, app), mode="local", fast=False)
    gate = _check_outcome(steps)
    if not gate.ok:
        gate.summary = "Not deployed. " + gate.summary
        return gate
    git = gitops.open_repo(root, token=ctx.token(), identity=auth.identity() or ("ctzd", "ctzd@localhost"))
    platform = ctx.platform_factory()
    changed = git.changed_paths()
    # After a rollback, a deploy with no new changes still needs a new build to put main back in production.
    paused = not changed and platform.status(app.app.sbu, app.app.name).paused
    summary = commit_message(changed, message)
    committed = git.commit_all(summary, allow_empty=paused)
    git.rebase_onto_remote()
    git.push_main()
    commit = git.head() or ""
    platform.resume(app.app.sbu, app.app.name)
    if detach:
        return Outcome(summary="Pushed; Jenkins is building it.", data={"commit": commit, "committed": committed}, next_steps=["Run `ctzd status --wait`."])
    build = wait_for_build(ctx, platform, app, commit, timeout=timeout)
    return _build_outcome(root, platform, app, commit, build)


def status(ctx: Context, *, wait: bool, timeout: float) -> Outcome:
    root, app = _app(ctx)
    platform = ctx.platform_factory()
    if wait:
        commit = gitops.open_repo(root, token=None, identity=None).head()
        if commit is None:
            msg = "This app has no commits yet."
            raise UsageError(msg)
        return _build_outcome(root, platform, app, commit, wait_for_build(ctx, platform, app, commit, timeout=timeout))
    current = platform.status(app.app.sbu, app.app.name)
    paused = " Auto-deploy is paused after a rollback; the next `ctzd deploy` resumes it." if current.paused else ""
    release_text = f"v{current.release}" if current.release is not None else "nothing yet"
    return Outcome(summary=f"{app.app.name} runs {release_text}.{paused}", data=current.model_dump())


def logs(ctx: Context, *, job: str | None, tail: int) -> Outcome:
    _root, app = _app(ctx)
    if job is not None:
        app.job(job)
    text = ctx.platform_factory().logs(app.app.sbu, app.app.name, job=job, tail=tail).text
    return Outcome(summary=text or "No log lines yet.", data={"text": text})


def releases(ctx: Context) -> Outcome:
    root, _app_config = _app(ctx)
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
    _root, app = _app(ctx)
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
    sync.require_not_ahead(root, assets.version())
    changed = sync.write(root, app, config.load(), assets.version())
    return Outcome(summary=f"Platform files are at version {assets.version()}." + (f" Updated {len(changed)} file(s)." if changed else ""), data={"changed": changed})


def delete(ctx: Context, *, confirmed: bool) -> Outcome:
    _root, app = _app(ctx)
    if not confirmed:
        msg = "Deleting an app removes its dashboard and jobs; its tables go after 30 days."
        raise UsageError(msg, hint="Confirm with the person, then run `ctzd delete --yes`.")
    ctx.platform_factory().delete(app.app.sbu, app.app.name)
    return Outcome(summary=f"Deleted {app.app.name}. Its tables are removed in 30 days.")


# ---------------------------------------------------------------- steps Jenkins runs


def ci_check(ctx: Context) -> Outcome:
    root, app = _app(ctx)
    return _check_outcome(ctx.run_checks(_target(root, app), mode="ci", fast=False))


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


def ci_smoke(ctx: Context, *, url: str, screenshot: Path) -> Outcome:
    _root, app = _app(ctx)
    from ctzd_cli import smoke  # noqa: PLC0415 - playwright comes with the optional ci extra, so import it only here.

    result = smoke.smoke(url, ui=app.app.ui, screenshot=screenshot)
    return Outcome(
        summary="The dashboard rendered without errors." if result.ok else "The dashboard shows errors: " + "; ".join(result.problems),
        data={"pages": result.pages, "problems": result.problems, "screenshot": str(screenshot)},
        ok=result.ok,
    )
