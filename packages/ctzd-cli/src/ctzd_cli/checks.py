"""`ctzd check`: every gate, the same locally and in CI. Locally a gate fixes what it can first; in CI it only checks."""

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import sqlglot
import sqlglot.errors
from packaging.version import Version

from ctzd_cli import assets, proc, rounding, sync
from ctzd_cli.appconfig import AppConfig
from ctzd_cli.config import PlatformConfig

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


def tool(root: Path, name: str, *args: str) -> proc.Completed:
    return proc.run([_uv(), "run", "--no-sync", name, *args], cwd=root, timeout=_TOOL_TIMEOUT)


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


def environment(root: Path, mode: Mode) -> Step:
    args = [_uv(), "sync", "--locked"] if mode == "ci" else [_uv(), "sync"]
    result = proc.run(args, cwd=root, timeout=_TOOL_TIMEOUT)
    if result.ok:
        return Step("environment", "passed")
    return Step("environment", "failed", proc.tail(result.output, 30))


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


def tests(root: Path) -> Step:
    # With no logic there is nothing to cover, and coverage of nothing reads 0%.
    floor = () if has_logic(root) else ("--cov-fail-under=0",)
    result = tool(root, "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "-p", "no:dash", *floor)
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


def run_all(target: Target, *, mode: Mode, fast: bool) -> list[Step]:
    """Every gate, in PLAN.md's order."""
    root = target.root
    steps = [platform_files(target, mode)]
    if steps[0].state == "failed" and mode == "ci":
        return steps
    steps.append(environment(root, mode))
    if steps[-1].state == "failed":
        return steps
    steps += [formatting(root, mode), lint(root, mode), types(root), layers(root), sql_files(root), numbers(root), app_toml(target)]
    steps.append(Step("tests", "skipped", "--fast") if fast else tests(root))
    steps.append(sample(root, mode))
    return steps
