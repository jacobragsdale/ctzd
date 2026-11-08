"""`ctzd check`: every gate, the same locally and in Jenkins."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import sqlglot
import sqlglot.errors
from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, ValidationError

from ctzd_cli import api_models as m
from ctzd_cli import proc, rounding, sync
from ctzd_cli.appconfig import AppConfig
from ctzd_cli.config import PlatformConfig
from ctzd_cli.errors import CliError
from ctzd_cli.releases import ReleaseSource, require_usable

Mode = Literal["local", "ci"]
State = Literal["passed", "fixed", "failed", "skipped"]
_PYFORMAT = re.compile(r"%\((\w+)\)s")
_TOOL_TIMEOUT = 900.0
CORE = "ctzd-core"


@dataclass(frozen=True)
class Target:
    """The app being checked, and where its platform releases come from."""

    root: Path
    app: AppConfig
    platform: PlatformConfig
    releases: ReleaseSource


@dataclass(frozen=True)
class Step:
    name: str
    state: State
    detail: str = ""
    # Files this step changed that belong in their own platform commit; see `ctzd deploy`.
    paths: tuple[str, ...] = ()


def _uv() -> str:
    return proc.which("uv", hint="Install uv: https://docs.astral.sh/uv/")


def _tool(root: Path, name: str, *args: str) -> proc.Completed:
    return proc.run([_uv(), "run", "--no-sync", name, *args], cwd=root, timeout=_TOOL_TIMEOUT)


class _Package(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    name: str
    version: str | None = None


class _Lock(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    package: tuple[_Package, ...] = ()


def locked_version(root: Path, package: str) -> Version | None:
    """The version of `package` in uv.lock; None when there is no lock or no such package."""
    path = root / "uv.lock"
    if not path.is_file():
        return None
    try:
        parsed: object = tomllib.loads(path.read_text(encoding="utf-8"))
        lock = _Lock.model_validate(parsed)
    except (tomllib.TOMLDecodeError, ValidationError):
        return None
    found = next((entry.version for entry in lock.package if entry.name == package and entry.version is not None), None)
    try:
        return None if found is None else Version(found)
    except InvalidVersion:
        return None


def core_problem(root: Path, release: m.PlatformRelease) -> str | None:
    locked = locked_version(root, CORE)
    if locked is None or locked >= Version(release.min_ctzd_core):
        return None
    return f"uv.lock has {CORE} {locked}; platform release {release.release} needs {release.min_ctzd_core} or newer."


def raise_core(target: Target, release: m.PlatformRelease) -> list[str]:
    """Upgrade ctzd-core and the SBU library in uv.lock when they are older than `release` needs."""
    if core_problem(target.root, release) is None:
        return []
    args = [_uv(), "lock", "--quiet"]
    for package in (CORE, Requirement(target.platform.sbu(target.app.app.sbu).library).name):
        args += ["--upgrade-package", package]
    result = proc.run(args, cwd=target.root, timeout=_TOOL_TIMEOUT)
    problem = core_problem(target.root, release)
    if not result.ok or problem is not None:
        msg = problem or f"uv could not upgrade {CORE}."
        raise CliError(msg, hint=proc.tail(result.output, 20))
    return ["uv.lock"]


def _described(err: CliError) -> str:
    return err.message if err.hint is None else f"{err.message} {err.hint}"


def _ci_platform_files(target: Target) -> Step:
    recorded = sync.recorded_release(target.root)
    if recorded is None:
        return Step("platform files", "failed", f"{sync.VERSION_FILE} records no platform release. Run `ctzd check` on a laptop and push the result.")
    try:
        release = require_usable(target.releases.get(recorded))
    except CliError as err:
        return Step("platform files", "failed", _described(err))
    problems = [f"{problem.path} ({problem.problem})" for problem in sync.drift(target.root, target.platform, release)]
    core = core_problem(target.root, release)
    if core is not None:
        problems.append(core)
    if problems:
        return Step("platform files", "failed", f"Platform-owned files differ from platform release {recorded}: {'; '.join(problems)}. Run `ctzd check` on a laptop.")
    return Step("platform files", "passed")


def _local_release(target: Target, recorded: int | None) -> m.PlatformRelease:
    """The release the app's build files should match: the channel's, when the platform answers."""
    current = target.releases.current(target.app.app.sbu, target.app.app.name)
    if current is not None:
        chosen = require_usable(target.releases.get(current.release))
    elif recorded is not None:
        chosen = require_usable(target.releases.get(recorded))
    else:
        chosen = require_usable(target.releases.newest_local())
    if recorded is None or recorded == chosen.release:
        return chosen
    try:
        base = require_usable(target.releases.get(recorded))
    except CliError:
        return chosen
    # A release that changes only guidance leaves the app on the release it records.
    return base if sync.same_build(target.platform, base, chosen) else chosen


def platform_files(target: Target, mode: Mode) -> Step:
    if mode == "ci":
        return _ci_platform_files(target)
    recorded = sync.recorded_release(target.root)
    try:
        release = _local_release(target, recorded)
        changed = sync.write_build(target.root, target.platform, release)
        changed += raise_core(target, release)
    except CliError as err:
        return Step("platform files", "failed", _described(err))
    sync.write_env_example(target.root, target.app)
    if not changed:
        return Step("platform files", "passed")
    moved = recorded != release.release
    verb = "Upgraded" if moved and recorded is not None and release.release > recorded else "Moved" if moved else "Restored"
    return Step("platform files", "fixed", f"{verb} platform files to platform release {release.release}: {', '.join(changed)}.", paths=tuple(changed))


def environment(root: Path, mode: Mode) -> Step:
    args = [_uv(), "sync", "--locked"] if mode == "ci" else [_uv(), "sync"]
    result = proc.run(args, cwd=root, timeout=_TOOL_TIMEOUT)
    if result.ok:
        return Step("environment", "passed")
    return Step("environment", "failed", proc.tail(result.output, 30))


def formatting(root: Path, mode: Mode) -> Step:
    if mode == "ci":
        result = _tool(root, "ruff", "format", "--check", ".")
        return Step("format", "passed") if result.ok else Step("format", "failed", proc.tail(result.output))
    result = _tool(root, "ruff", "format", ".")
    if not result.ok:
        return Step("format", "failed", proc.tail(result.output))
    reformatted = re.search(r"(\d+) files? reformatted", result.output)
    return Step("format", "fixed", f"{reformatted.group(1)} file(s) reformatted.") if reformatted else Step("format", "passed")


def lint(root: Path, mode: Mode) -> Step:
    args = ("check", ".") if mode == "ci" else ("check", "--fix", ".")
    result = _tool(root, "ruff", *args)
    fixed = re.search(r"Found \d+ errors? \((\d+) fixed, 0 remaining\)", result.output)
    if result.ok:
        return Step("lint", "fixed", f"{fixed.group(1)} problem(s) fixed automatically.") if fixed else Step("lint", "passed")
    return Step("lint", "failed", proc.tail(result.output))


def types(root: Path) -> Step:
    result = _tool(root, "ty", "check", "--output-format", "concise")
    return Step("types", "passed") if result.ok else Step("types", "failed", proc.tail(result.output))


def layers(root: Path) -> Step:
    result = _tool(root, "lint-imports", "--no-cache")
    return Step("layers", "passed") if result.ok else Step("layers", "failed", proc.tail(result.output, 40))


def sql_problem(text: str) -> str | None:
    """None when the SQL parses as Oracle or as SQL Server; otherwise the parser's complaint."""
    attempts = (("oracle", text), ("tsql", _PYFORMAT.sub(r"@\1", text)))
    complaint = ""
    for dialect, source in attempts:
        try:
            statements = [statement for statement in sqlglot.parse(source, read=dialect) if statement is not None]
        except sqlglot.errors.ParseError as err:
            complaint = str(err).splitlines()[0]
            continue
        if not statements:
            return "the file has no SQL statement"
        return None
    return complaint


def sql_files(root: Path) -> Step:
    problems = []
    for path in sorted((root / "src" / "app" / "data" / "sql").glob("**/*.sql")):
        problem = sql_problem(path.read_text(encoding="utf-8"))
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
    lines = [*shown, *([f"... and {more} more."] if more else []), rounding.FIX]
    return Step("numbers", "failed", "\n".join(lines))


def tests(root: Path) -> Step:
    result = _tool(root, "pytest", "-q", "--no-header", "-p", "no:cacheprovider")
    return Step("tests", "passed", proc.tail(result.output, 3)) if result.ok else Step("tests", "failed", proc.tail(result.output, 80))


def run_all(target: Target, *, mode: Mode, fast: bool) -> list[Step]:
    root = target.root
    steps = [platform_files(target, mode)]
    if steps[0].state == "failed" and mode == "ci":
        return steps
    steps.append(environment(root, mode))
    if steps[-1].state == "failed":
        return steps
    steps += [formatting(root, mode), lint(root, mode), types(root), layers(root), sql_files(root), numbers(root)]
    steps.append(Step("tests", "skipped", "--fast") if fast else tests(root))
    return steps
