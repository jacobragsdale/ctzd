"""`ctzd check`: every gate, the same locally and in Jenkins."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import sqlglot
import sqlglot.errors

from ctzd_cli import proc, sync
from ctzd_cli.appconfig import AppConfig
from ctzd_cli.config import PlatformConfig

Mode = Literal["local", "ci"]
State = Literal["passed", "fixed", "failed", "skipped"]
_PYFORMAT = re.compile(r"%\((\w+)\)s")
_TOOL_TIMEOUT = 900.0


@dataclass(frozen=True)
class Target:
    """The app being checked, and the platform version its files must match."""

    root: Path
    app: AppConfig
    platform: PlatformConfig
    version: str


@dataclass(frozen=True)
class Step:
    name: str
    state: State
    detail: str = ""


def _uv() -> str:
    return proc.which("uv", hint="Install uv: https://docs.astral.sh/uv/")


def _tool(root: Path, name: str, *args: str) -> proc.Completed:
    return proc.run([_uv(), "run", "--no-sync", name, *args], cwd=root, timeout=_TOOL_TIMEOUT)


def platform_files(target: Target, mode: Mode) -> Step:
    root, app, platform, version = target.root, target.app, target.platform, target.version
    recorded = sync.recorded_version(root)
    comparison = sync.compare_versions(recorded, version)
    if comparison == "ahead":
        return Step("platform files", "failed", f"The app uses platform version {recorded}; upgrade ctzd with `uv tool upgrade ctzd`.")
    problems = sync.drift(root, app, platform, version)
    if not problems and comparison == "same":
        sync.mirror_skills_for_claude(root)
        return Step("platform files", "passed")
    if mode == "ci":
        listed = "; ".join(f"{problem.path} ({problem.problem})" for problem in problems)
        return Step("platform files", "failed", f"Platform-owned files differ from version {version}: {listed}. Run `ctzd sync`.")
    changed = sync.write(root, app, platform, version)
    return Step("platform files", "fixed", f"Restored platform-owned files to version {version}: {', '.join(changed)}.")


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
    steps += [formatting(root, mode), lint(root, mode), types(root), layers(root), sql_files(root)]
    steps.append(Step("tests", "skipped", "--fast") if fast else tests(root))
    return steps
