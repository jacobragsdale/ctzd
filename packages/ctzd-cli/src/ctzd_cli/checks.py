"""`ctzd check`: every gate, the same locally and in CI. Locally a gate fixes what it can first; in CI it only checks."""

import ast
import datetime as dt
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import sqlglot
import sqlglot.errors
import tomlkit
from packaging.requirements import Requirement
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, ValidationError

from ctzd_cli import assets, kitfacts, proc, rounding, sync
from ctzd_cli.appconfig import AppConfig
from ctzd_cli.config import PlatformConfig
from ctzd_cli.errors import CliError
from ctzd_cli.kitfacts import KitFacts

Mode = Literal["local", "ci"]
State = Literal["passed", "fixed", "failed", "skipped"]
_PYFORMAT = re.compile(r"%\((\w+)\)s")
_TOOL_TIMEOUT = 900.0
SAMPLE_MARKER = "ctzd: sample"
"""The first line of the template's sample page and sample job: an app that still has one is not ready to ship."""


@dataclass(frozen=True)
class Target:
    """The app being checked."""

    root: Path
    app: AppConfig
    platform: PlatformConfig


@dataclass(frozen=True)
class Step:
    name: str
    state: State
    detail: str = ""
    # Files this step changed that belong in their own platform commit; see `ctzd deploy`.
    paths: tuple[str, ...] = ()
    # Something the agent should tell the person or act on although the step passed.
    warning: str | None = None


def _uv() -> str:
    return proc.which("uv", hint="Install uv: https://docs.astral.sh/uv/")


def tool(root: Path, name: str, *args: str, env: dict[str, str] | None = None) -> proc.Completed:
    return proc.run([_uv(), "run", "--no-sync", name, *args], cwd=root, env=env, timeout=_TOOL_TIMEOUT)


def fake_env(target: Target) -> dict[str, str]:
    """What the app's tests, its pages' drive and its jobs' runs see: every source serving its sample rows, the store
    and the outbox in scratch folders, and no message sent."""
    root, app = target.root, target.app
    return {
        "CTZD_APP": app.app.name,
        "CTZD_SBU": app.app.sbu,
        "CTZD_ENV": "local",
        "CTZD_FAKE": "1",
        "CTZD_OWNERS": ",".join(app.app.owners),
        "CTZD_FIXTURES": str(root / "fixtures"),
        "CTZD_STORE_URL": str(root / ".ctzd" / "out" / "check-store"),
        "CTZD_OUTBOX": str(root / ".ctzd" / "out" / "check-outbox"),
    }


def platform_files(target: Target, mode: Mode) -> Step:
    """The build files are this CLI's. Locally they are rewritten, which moves the app to this CLI; in CI they must
    already be, and CI must run the CLI version the app records."""
    root, running = target.root, assets.version()
    recorded = sync.recorded_version(root)
    if recorded is not None and recorded > Version(running):
        detail = f"This app's build files are from ctzd {recorded}, newer than this ctzd {running}."
        return Step("platform files", "failed", f"{detail} Run `uv tool upgrade ctzd`, then the same command again.")
    if mode == "ci":
        if recorded is None or str(recorded) != running:
            return Step("platform files", "failed", f"{sync.VERSION_FILE} records ctzd {recorded}, and CI ran ctzd {running}: CI must run the version the app records.")
        problems = [f"{item.path} ({item.problem})" for item in sync.drift(root, target.platform, target.app)]
        if problems:
            return Step("platform files", "failed", f"Platform-owned files differ from ctzd {running}'s: {'; '.join(problems)}. Run `ctzd check` on a desktop and push the result.")
        return Step("platform files", "passed")
    changed = sync.write_build(root, target.platform, target.app)
    sync.write_env_example(root, target.app)
    if not changed:
        return Step("platform files", "passed")
    verb = "Upgraded" if recorded is not None and recorded < Version(running) else "Restored"
    return Step("platform files", "fixed", f"{verb} the platform files to ctzd {running}: {', '.join(changed)}.", paths=tuple(changed))


def environment(target: Target, mode: Mode) -> Step:
    """The app's packages installed: exactly uv.lock's in CI. Locally the app first moves to the newest release of its
    kit within the major version it is on, when the index can be asked."""
    root = target.root
    if mode == "local":
        kit = target.platform.sbu(target.app.app.sbu).kit_package
        proc.run([_uv(), "lock", "--quiet", "--upgrade-package", kit], cwd=root, timeout=_TOOL_TIMEOUT)
    args = [_uv(), "sync", "--locked"] if mode == "ci" else [_uv(), "sync"]
    result = proc.run(args, cwd=root, timeout=_TOOL_TIMEOUT)
    if result.ok:
        return Step("environment", "passed")
    return Step("environment", "failed", proc.tail(result.output, 30))


def kit(target: Target, mode: Mode) -> tuple[Step, KitFacts | None]:
    """The app's kit is its department's, and the files that come from it (`.importlinter`, and the department's
    skill) are what it says: written locally, checked in CI."""
    root, app = target.root, target.app
    try:
        found = kitfacts.facts(root, mode="fresh" if mode == "ci" else "cached")
    except CliError as err:
        return Step("kit", "failed", err.message if err.hint is None else f"{err.message}\n{err.hint}"), None
    if found is None:
        return Step("kit", "failed", "The app's packages are not installed; the environment step installs them."), None
    if found.sbu != app.app.sbu:
        return Step("kit", "failed", f"The app's kit is {found.kit}, {found.title}'s, and app.toml says sbu {app.app.sbu!r}: an app depends on its own department's kit alone."), found
    return _kit_files(target, mode, found), found


def _kit_files(target: Target, mode: Mode, found: KitFacts) -> Step:
    root, app = target.root, target.app
    tokens = kitfacts.tokens(found)
    if mode == "ci":
        problems = [f"{item.path} ({item.problem})" for item in sync.drift(root, target.platform, app, tokens)]
        if problems:
            return Step("kit", "failed", f"Files written from the kit differ: {'; '.join(problems)}. Run `ctzd check` on a desktop and push the result.")
        return Step("kit", "passed")
    changed = sync.write_build(root, target.platform, app, tokens)
    sync.write_guidance(root, kitfacts.skill_files(found))
    if changed:
        return Step("kit", "fixed", f"Wrote {', '.join(changed)} from {found.kit}.", paths=tuple(changed))
    return Step("kit", "passed")


def sql_dialects(root: Path, found: KitFacts | None) -> dict[str, tuple[str, ...]]:
    """Each SQL file's dialect, by its name, from the `query(SOURCE, FILE, ...)` calls in `src/app/data/`."""
    if found is None:
        return {}
    dialects: dict[str, tuple[str, ...]] = {}
    for path in sorted((root / "src" / "app" / "data").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            name = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id if isinstance(call.func, ast.Name) else None
            arguments = [argument.value for argument in call.args[:2] if isinstance(argument, ast.Constant) and isinstance(argument.value, str)]
            if name != "query" or len(arguments) != 2:  # noqa: PLR2004 - the source and the file
                continue
            source = found.source(str(arguments[0]))
            if source is not None and source.dialect is not None:
                dialects[Path(str(arguments[1])).name] = (source.dialect,)
    return dialects


def formatting(root: Path, mode: Mode) -> Step:
    if mode == "ci":
        result = tool(root, "ruff", "format", "--check", ".")
        return Step("format", "passed") if result.ok else Step("format", "failed", proc.tail(result.output))
    result = tool(root, "ruff", "format", ".")
    if not result.ok:
        return Step("format", "failed", proc.tail(result.output))
    reformatted = re.search(r"(\d+) files? reformatted", result.output)
    return Step("format", "fixed", f"{reformatted.group(1)} file(s) reformatted.") if reformatted else Step("format", "passed")


def lint(root: Path, mode: Mode) -> Step:
    args = ("check", ".") if mode == "ci" else ("check", "--fix", ".")
    result = tool(root, "ruff", *args)
    fixed = re.search(r"Found \d+ errors? \((\d+) fixed, 0 remaining\)", result.output)
    if result.ok:
        return Step("lint", "fixed", f"{fixed.group(1)} problem(s) fixed automatically.") if fixed else Step("lint", "passed")
    return Step("lint", "failed", proc.tail(result.output))


def types(root: Path) -> Step:
    result = tool(root, "ty", "check", "--output-format", "concise")
    return Step("types", "passed") if result.ok else Step("types", "failed", proc.tail(result.output))


def layers(root: Path) -> Step:
    result = tool(root, "lint-imports", "--no-cache")
    return Step("layers", "passed") if result.ok else Step("layers", "failed", proc.tail(result.output, 40))


def sql_problem(text: str, dialects: tuple[str, ...] = ("oracle", "tsql")) -> str | None:
    """None when the SQL parses in one of `dialects`; otherwise the parser's complaint."""
    complaint = ""
    for dialect in dialects:
        source = _PYFORMAT.sub(r"@\1", text) if dialect == "tsql" else text
        try:
            statements = [statement for statement in sqlglot.parse(source, read=dialect) if statement is not None]
        except sqlglot.errors.ParseError as err:
            complaint = str(err).splitlines()[0]
            continue
        return None if statements else "the file has no SQL statement"
    return complaint


def sql_files(root: Path, dialects: dict[str, tuple[str, ...]] | None = None) -> Step:
    """Each `src/app/data/sql/*.sql` parses, in its source's dialect where `dialects` knows it, by the file's name."""
    problems = []
    for path in sorted((root / "src" / "app" / "data" / "sql").glob("**/*.sql")):
        known = (dialects or {}).get(path.name)
        problem = sql_problem(path.read_text(encoding="utf-8"), *([] if known is None else [known]))
        if problem is not None:
            problems.append(f"{path.relative_to(root).as_posix()}: {problem}")
    return Step("sql", "failed", "\n".join(problems)) if problems else Step("sql", "passed")


_SHOWN_PROBLEMS = 20


def numbers(root: Path) -> Step:
    """No Python or SQL in the app rounds or formats a number; see `ctzd_cli.rounding`."""
    found = rounding.problems(root)
    if not found:
        return Step("numbers", "passed")
    shown = found[:_SHOWN_PROBLEMS]
    more = len(found) - len(shown)
    return Step("numbers", "failed", "\n".join([*shown, *([f"... and {more} more."] if more else []), rounding.FIX]))


def repository_name(root: Path) -> str | None:
    """The app's repository's name: CI's, or the git remote's; None for an app with no repository."""
    given = os.environ.get("GITHUB_REPOSITORY", "")
    if given:
        return given.rpartition("/")[2]
    config = root / ".git" / "config"
    if not config.is_file():
        return None
    found = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', config.read_text(encoding="utf-8"))
    if found is None:
        return None
    url = str(found.group(1))
    return url.rstrip("/").rpartition("/")[2].removesuffix(".git")


def _has_main(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return True  # the lint step reports it
    return any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body)


def app_toml(target: Target, known_sources: frozenset[str] | None = None) -> Step:
    """app.toml names this repository, every job has a module with `main()`, a job app has a job, and every source in
    `[sources].use` is one the kit has (`known_sources`, when the kit could be asked)."""
    app, root = target.app, target.root
    problems: list[str] = []
    expected = target.platform.repo_name(app.app.sbu, app.app.name)
    repository = repository_name(root)
    if repository is not None and repository != expected:
        problems.append(f"app.toml names the app {expected!r} (sbu {app.app.sbu!r}, name {app.app.name!r}), but its repository is {repository!r}: put back the sbu and name it was created with.")
    if app.app.kind == "job" and not app.jobs:
        problems.append("A job app needs at least one [[jobs]] entry in app.toml.")
    for job in app.jobs:
        module = root / "src" / "app" / "jobs" / f"{job.name}.py"
        if not module.is_file():
            problems.append(f"Job {job.name!r} has no module: write src/app/jobs/{job.name}.py with `def main() -> int:`.")
        elif not _has_main(module):
            problems.append(f"src/app/jobs/{job.name}.py has no top-level `def main() -> int:`, which Airflow calls.")
    if known_sources is not None:
        problems += [f"[sources].use names {source!r}, which the kit does not have; `ctzd sources` lists the ones it does." for source in app.sources.use if source not in known_sources]
    return Step("app.toml", "failed", "\n".join(problems)) if problems else Step("app.toml", "passed")


def has_logic(root: Path) -> bool:
    """Whether `src/app/logic` holds any code; the coverage floor applies only to code that exists."""
    folder = root / "src" / "app" / "logic"
    return any(path.read_text(encoding="utf-8").strip() for path in folder.rglob("*.py")) if folder.is_dir() else False


_DEPRECATION = re.compile(r"^\s*(?P<where>\S+?:\d+): (?P<kind>DeprecationWarning|PendingDeprecationWarning|FutureWarning): (?P<message>.+)$", re.MULTILINE)
_SHOWN_DEPRECATIONS = 5


def deprecations(output: str) -> list[str]:
    """The deprecations pytest's warnings summary lists, once each: a dependency will remove something the app uses."""
    found: list[str] = []
    for match in _DEPRECATION.finditer(output):
        line = f"{match.group('where')}: {match.group('kind')}: {match.group('message').strip()}"
        if line not in found:
            found.append(line)
    return found


def tests(target: Target) -> Step:
    root = target.root
    # With no logic there is nothing to cover, and coverage of nothing reads 0%.
    floor = () if has_logic(root) else ("--cov-fail-under=0",)
    result = tool(root, "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "-p", "no:dash", *floor, env=fake_env(target))
    if not result.ok:
        return Step("tests", "failed", proc.tail(result.output, 80))
    found = deprecations(result.output)
    if not found:
        return Step("tests", "passed", proc.tail(result.output, 3))
    shown = found[:_SHOWN_DEPRECATIONS] + ([f"... and {len(found) - _SHOWN_DEPRECATIONS} more."] if len(found) > _SHOWN_DEPRECATIONS else [])
    note = "The tests pass, but a package the app uses has deprecated something it calls. It still works; change the code before that package removes it:"
    return Step("tests", "passed", "\n".join([proc.tail(result.output, 3), note, *shown]), warning=note)


def sample(root: Path, mode: Mode) -> Step:
    """The template's sample page and sample job, left in place, never ship: CI fails on them, and `ctzd deploy`
    refuses them."""
    found = sorted(
        path.relative_to(root).as_posix()
        for folder in ("pages", "jobs")
        for path in (root / "src" / "app" / folder).glob("*.py")
        if SAMPLE_MARKER in path.read_text(encoding="utf-8").partition("\n")[0]
    )
    if not found:
        return Step("sample", "passed")
    detail = f"{', '.join(found)} is still the template's sample. Replace it with what the person asked for and delete its first line; until then the app is not deployed."
    return Step("sample", "failed", detail) if mode == "ci" else Step("sample", "passed", detail, warning=detail)


DRIVE = Path(__file__).parent / "inapp" / "drive.py"
"""The page driving, checks and render, which run inside the app's own environment."""


def drive(target: Target, *args: str) -> tuple[proc.Completed, dict[str, object] | None]:
    """`python drive.py ARGS` in the app's environment, on the sample rows, and the JSON it printed last."""
    result = proc.run([_uv(), "run", "--no-sync", "python", str(DRIVE), *args], cwd=target.root, env=fake_env(target), timeout=_TOOL_TIMEOUT)
    last = result.output.strip().splitlines()[-1] if result.output.strip() else ""
    try:
        found: object = json.loads(last) if last.startswith("{") else None
    except ValueError:
        found = None
    return result, ({str(key): value for key, value in found.items()} if isinstance(found, dict) else None)


def pages(target: Target) -> Step:
    """Gate 10: the dashboard driven on its sample rows with no browser (`inapp/drive.py`)."""
    result, found = drive(target, "check")
    if found is None:
        return Step("pages", "failed", f"The page checks did not run: {proc.tail(result.output, 30)}")
    problems = found.get("problems")
    listed = [f"{item[0]}: {item[1]}." for item in problems if isinstance(item, list) and len(item) == 2] if isinstance(problems, list) else []  # noqa: PLR2004 - what, and its fix
    return Step("pages", "failed", "\n".join(listed)) if listed else Step("pages", "passed")


def jobs(target: Target) -> Step:
    """Gate 11: each job's `main()` run once on the sample rows, writing to a scratch store and sending nothing."""
    problems: list[str] = []
    for job in target.app.jobs:
        command = ("python", "-c", f"import sys; from app.jobs.{job.name} import main; sys.exit(main())")
        result = proc.run([_uv(), "run", "--no-sync", *command], cwd=target.root, env=fake_env(target) | {"CTZD_MESSAGES": "off"}, timeout=_TOOL_TIMEOUT)
        if not result.ok:
            problems.append(f"Job {job.name} failed on the sample rows (exit {result.code}); run `ctzd run {job.name} --local` to see it:\n{proc.tail(result.output, 20)}")
    return Step("jobs", "failed", "\n".join(problems)) if problems else Step("jobs", "passed")


def run_all(target: Target, *, mode: Mode, fast: bool, upgrade: bool = True, now: dt.datetime | None = None) -> list[Step]:
    """Every gate, in PLAN.md's order; locally, with every test run and every gate passed, the kit's next major version
    is tried (`next_major`)."""
    root = target.root
    steps = [platform_files(target, mode)]
    if steps[0].state == "failed" and mode == "ci":
        return steps
    steps.append(environment(target, mode))
    if steps[-1].state == "failed":
        return steps
    found_kit, found = kit(target, mode)
    steps.append(found_kit)
    known = None if found is None else frozenset(source.name for source in found.sources)
    steps += [formatting(root, mode), lint(root, mode), types(root), layers(root), sql_files(root, sql_dialects(root, found)), numbers(root), app_toml(target, known)]
    steps.append(Step("tests", "skipped", "--fast") if fast else tests(target))
    # The jobs run before the pages, into the same scratch store, so a page that reads a job's table finds it.
    if target.app.jobs:
        steps.append(Step("jobs", "skipped", "--fast") if fast else jobs(target))
    if target.app.app.kind == "dash":
        steps.append(pages(target))
    steps.append(sample(root, mode))
    if mode == "ci" or fast or not upgrade:
        return steps
    return next_major(target, steps, now=now or dt.datetime.now(dt.UTC))


# ---------------------------------------------------------------- a kit's next major version

KIT_UPGRADE = ".ctzd/kit-upgrade.json"
_RETRY_MAJOR = dt.timedelta(days=1)


class _Tried(BaseModel):
    """A major version of the kit this app did not pass, and when: tried again a day later, not on every check."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    major: int
    tried_at: dt.datetime
    failed: tuple[str, ...]


def _locked_major(root: Path, package: str) -> int | None:
    path = root / "uv.lock"
    if not path.is_file():
        return None
    for entry in tomllib.loads(path.read_text(encoding="utf-8")).get("package", []):
        if isinstance(entry, dict) and entry.get("name") == package and isinstance(entry.get("version"), str):
            return Version(entry["version"]).major
    return None


def _require(root: Path, package: str, major: int) -> None:
    """Make the app's requirement of `package` the major version `major`, in pyproject.toml."""
    path = root / "pyproject.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    project = document.get("project")
    dependencies = project.get("dependencies") if isinstance(project, dict) else None
    if not isinstance(dependencies, list):
        return
    for index, text in enumerate(dependencies):
        if Requirement(str(text)).name == package:
            dependencies[index] = f"{package}>={major},<{major + 1}"
    path.write_text(tomlkit.dumps(document), encoding="utf-8")


def next_major(target: Target, steps: list[Step], *, now: dt.datetime) -> list[Step]:
    """Locally, once a day at most: move the app to its kit's next major version when the index has one and every
    check passes on it; otherwise stay, put the files back, and say so (PLAN.md section 7, "Reaching apps")."""
    root = target.root
    package = target.platform.sbu(target.app.app.sbu).kit_package
    record = root / KIT_UPGRADE
    major = _locked_major(root, package)
    if major is None or any(step.state == "failed" for step in steps):
        return steps
    try:
        tried = _Tried.model_validate_json(record.read_bytes()) if record.is_file() else None
    except ValidationError:
        tried = None
    if tried is not None and tried.major == major + 1 and now - tried.tried_at < _RETRY_MAJOR:
        return [*steps, Step("kit version", "passed", warning=_stay(package, major, tried.failed))]
    saved = {name: (root / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    _require(root, package, major + 1)
    if not proc.run([_uv(), "lock", "--quiet"], cwd=root, timeout=_TOOL_TIMEOUT).ok:
        for name, data in saved.items():
            (root / name).write_bytes(data)
        return steps  # no next major version on the index, or the index cannot be asked
    moved = run_all(target, mode="local", fast=False, upgrade=False)
    failed = tuple(step.name for step in moved if step.state == "failed")
    if not failed:
        record.unlink(missing_ok=True)
        return [*moved, Step("kit version", "fixed", f"Moved to {package} {major + 1}, its next major version, on which every check passes.", paths=("pyproject.toml", "uv.lock"))]
    for name, data in saved.items():
        (root / name).write_bytes(data)
    environment(target, "local")
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(_Tried(major=major + 1, tried_at=now, failed=failed).model_dump_json(), encoding="utf-8")
    return [*steps, Step("kit version", "passed", warning=_stay(package, major, failed))]


def _stay(package: str, major: int, failed: tuple[str, ...]) -> str:
    return (
        f"{package} {major + 1} is out, and this app does not pass its checks on it yet ({', '.join(failed)}), so it stays on {package} {major}. "
        "Tell the person; the kit's maintainers can say what changed."
    )
