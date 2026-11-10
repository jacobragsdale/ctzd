"""`ctzd check`: every gate, the same locally and in Jenkins."""

import ast
import datetime as dt
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
from ctzd_cli import assets, node, proc, rounding, sync
from ctzd_cli.appconfig import AppConfig, Ui
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
    # Something the agent should tell the person or act on although the step passed.
    warning: str | None = None


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
    except tomllib.TOMLDecodeError, ValidationError:
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
        return Step("platform files", "failed", f"{sync.VERSION_FILE} records no platform release. Run `ctzd check` on a desktop and push the result.")
    try:
        release = require_usable(target.releases.get(recorded))
    except CliError as err:
        return Step("platform files", "failed", _described(err))
    problems = [f"{problem.path} ({problem.problem})" for problem in sync.drift(target.root, target.platform, release, target.app.app.ui)]
    core = core_problem(target.root, release)
    if core is not None:
        problems.append(core)
    if problems:
        return Step("platform files", "failed", f"Platform-owned files differ from platform release {recorded}: {'; '.join(problems)}. Run `ctzd check` on a desktop.")
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
    return base if sync.same_build(target.platform, base, chosen, target.app.app.ui) else chosen


# The build files that decide what the checks refuse: a release that changes one of them, a dev tool pin, or the npm
# pins (basis-ui's lint and components) is breaking, whether or not its manifest says so.
_CHECK_FILES = frozenset({"ruff.toml", "ty.toml", "pytest.toml", ".importlinter", "web/vite.config.ts", "web/tsconfig.json"})


def is_breaking(platform: PlatformConfig, base: m.PlatformRelease, chosen: m.PlatformRelease, ui: Ui) -> bool:
    """Whether moving from `base` to `chosen` can make an app that passes today fail."""
    if chosen.breaking:
        return True
    one, other = assets.locked_files(base, ui), assets.locked_files(chosen, ui)
    files = any(one.get(path) != other.get(path) for path in _CHECK_FILES)
    pins = base.python.dev != chosen.python.dev or (ui == "react" and sync.npm_pins(platform, base) != sync.npm_pins(platform, chosen))
    return files or pins


def platform_files(target: Target, mode: Mode) -> Step:
    if mode == "ci":
        return _ci_platform_files(target)
    recorded = sync.recorded_release(target.root)
    try:
        release = _local_release(target, recorded)
    except CliError as err:
        return Step("platform files", "failed", _described(err))
    return platform_files_at(target, release)


def platform_files_at(target: Target, release: m.PlatformRelease) -> Step:
    """Bring the app's build files to `release`, locally."""
    recorded = sync.recorded_release(target.root)
    try:
        changed = sync.write_build(target.root, target.platform, release, target.app.app.ui)
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


# What only `src/app/data/` may call: a dataset or a job reaches a source or a table through a loader.
_READS = frozenset({"query", "rest", "read_table"})


def entry_point_reads(root: Path) -> list[str]:
    """Each place a dataset or a job reads a source or a table itself, which import-linter cannot see: both import `ctzd`."""
    found: list[str] = []
    for folder in ("api", "jobs"):
        for path in sorted((root / "src" / "app" / folder).glob("**/*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue  # the lint step reports it
            shown = path.relative_to(root).as_posix()
            for call in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
                func = call.func
                if isinstance(func, ast.Attribute) and func.attr in _READS and isinstance(func.value, ast.Name) and func.value.id == "ctzd":
                    found.append(f"{shown}:{call.lineno}: ctzd.{func.attr}() reads data outside src/app/data/; call a loader there instead (ctzd-data).")
    return found


def layers(root: Path) -> Step:
    result = _tool(root, "lint-imports", "--no-cache")
    reads = entry_point_reads(root)
    if result.ok and not reads:
        return Step("layers", "passed")
    return Step("layers", "failed", "\n".join([*([] if result.ok else [proc.tail(result.output, 40)]), *reads]))


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


WEB = "web"
_WRITE_OPENAPI = "from pathlib import Path; import ctzd.api; print(ctzd.api.write_openapi(Path('web/openapi.json')))"


def _pnpm(root: Path, *args: str) -> proc.Completed:
    pnpm = node.which("pnpm")
    if pnpm is None:
        msg = "pnpm is not installed."
        raise CliError(msg, hint="Run `ctzd setup`, which installs Node and pnpm.")
    return proc.run([pnpm, *args], cwd=root / WEB, env=node.env(), timeout=_TOOL_TIMEOUT)


def web_install(root: Path, mode: Mode) -> Step:
    """The page's npm packages, from the app's lockfile in Jenkins."""
    result = _pnpm(root, "install", "--frozen-lockfile") if mode == "ci" else _pnpm(root, "install")
    return Step("web install", "passed") if result.ok else Step("web install", "failed", proc.tail(result.output, 30))


def client(root: Path, mode: Mode) -> Step:
    """The page's client matches the datasets: `web/openapi.json` from `src/app/api/datasets.py`, and the client
    generated from it. Locally both are rewritten; Jenkins fails when either is out of date."""
    written = proc.run([_uv(), "run", "--no-sync", "python", "-c", _WRITE_OPENAPI], cwd=root, timeout=_TOOL_TIMEOUT)
    if not written.ok:
        return Step("client", "failed", proc.tail(written.output, 30))
    changed = written.output.strip().endswith("True")
    if mode == "ci":
        if changed:
            return Step("client", "failed", "web/openapi.json does not match the datasets. Run `ctzd check` on a desktop and push the result.")
        result = _pnpm(root, "exec", "vp", "run", "client-check")
        return Step("client", "passed") if result.ok else Step("client", "failed", proc.tail(result.output, 30))
    result = _pnpm(root, "exec", "vp", "run", "client-pull")
    if not result.ok:
        return Step("client", "failed", proc.tail(result.output, 30))
    return Step("client", "fixed", "Regenerated the page's client from the datasets.") if changed else Step("client", "passed")


_SUPPRESSION = re.compile(r"(?:oxlint|eslint)-disable(?:-next-line|-line)?(?P<rules>[^\n*]*)|@ts-(?:expect-error|ignore|nocheck)")
_ALLOWED_SUPPRESSION = "oxc/no-barrel-file"


def web_suppressions(root: Path) -> list[str]:
    """Each comment in the pages that turns a check off, which basis-ui's lint refuses: only `oxc/no-barrel-file` in
    `src/api` may be disabled. Read as text, as basis-ui's `check-full` does, since a comment that turns off every rule
    hides from the lint itself; ctzd runs `vp check`, not `check-full`, which expects an app `vp run sync` writes."""
    found: list[str] = []
    source = root / WEB / "src"
    for path in sorted(item for pattern in ("*.ts", "*.tsx") for item in source.rglob(pattern)):
        relative = path.relative_to(root / WEB).as_posix()
        if relative.startswith("src/api/generated/"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in _SUPPRESSION.finditer(line):
                rules = (match.group("rules") or "").split("--", 1)[0].strip()
                if relative.startswith("src/api/") and rules == _ALLOWED_SUPPRESSION:
                    continue
                found.append(f"web/{relative}:{number}: `{match.group(0).strip()}` turns a check off; fix what it reports instead (ctzd-pages).")
    return found


def web_check(root: Path, mode: Mode) -> Step:
    """The pages' formatting, lint and types, at basis-ui's settings, and no comment that turns a check off; locally it
    fixes what it can first."""
    result = _pnpm(root, "exec", "vp", "check") if mode == "ci" else _pnpm(root, "exec", "vp", "check", "--fix")
    suppressed = web_suppressions(root)
    if result.ok and not suppressed:
        return Step("web check", "passed")
    return Step("web check", "failed", "\n".join([*([] if result.ok else [proc.tail(result.output, 60)]), *suppressed]))


def web_build(root: Path) -> Step:
    """The pages built into `web/dist`, which the app serves at `/`; the React Compiler fails the build on a page it
    cannot optimize."""
    result = _pnpm(root, "exec", "vp", "build")
    return Step("web build", "passed") if result.ok else Step("web build", "failed", proc.tail(result.output, 40))


_DEPRECATION = re.compile(r"^\s*(?P<where>\S+?:\d+): (?P<kind>DeprecationWarning|PendingDeprecationWarning|FutureWarning): (?P<message>.+)$", re.MULTILINE)
_SHOWN_DEPRECATIONS = 5


def has_logic(root: Path) -> bool:
    """Whether `src/app/logic` holds any code; the coverage floor applies only to code that exists."""
    folder = root / "src" / "app" / "logic"
    return any(path.read_text(encoding="utf-8").strip() for path in folder.rglob("*.py")) if folder.is_dir() else False


def deprecations(output: str) -> list[str]:
    """The deprecations pytest's warnings summary lists, once each: a dependency will remove something the app uses."""
    found: list[str] = []
    for match in _DEPRECATION.finditer(output):
        line = f"{match.group('where')}: {match.group('kind')}: {match.group('message').strip()}"
        if line not in found:
            found.append(line)
    return found


SAMPLE_MARKER = "ctzd: sample page"
"""The first line of the starter's sample page: an app still showing it is not ready to ship."""


def sample_pages(root: Path, mode: Mode) -> Step:
    """The starter's sample page, left in place, never ships: Jenkins fails on it, and `ctzd deploy` refuses it."""
    found = sorted(path.relative_to(root).as_posix() for path in (root / WEB / "src").rglob("*.tsx") if SAMPLE_MARKER in path.read_text(encoding="utf-8"))
    if not found:
        return Step("sample pages", "passed")
    detail = f"{', '.join(found)} is still the starter's sample page. Replace it with the person's own page and delete its first comment line; until then the app is not deployed."
    if mode == "ci":
        return Step("sample pages", "failed", detail)
    return Step("sample pages", "passed", detail, warning=detail)


def tests(root: Path) -> Step:
    # With no logic there is nothing to cover, and coverage of nothing reads 0%.
    floor = () if has_logic(root) else ("--cov-fail-under=0",)
    result = _tool(root, "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *floor)
    if not result.ok:
        return Step("tests", "failed", proc.tail(result.output, 80))
    found = deprecations(result.output)
    if not found:
        return Step("tests", "passed", proc.tail(result.output, 3))
    shown = found[:_SHOWN_DEPRECATIONS] + ([f"... and {len(found) - _SHOWN_DEPRECATIONS} more."] if len(found) > _SHOWN_DEPRECATIONS else [])
    note = "The tests pass, but a package the app uses has deprecated something it calls. It still works; change the code before that package removes it:"
    return Step("tests", "passed", "\n".join([proc.tail(result.output, 3), note, *shown]), warning=note)


UPGRADE_RECORD = ".ctzd/upgrade.json"
_RETRY_BREAKING = dt.timedelta(days=1)


class _Tried(BaseModel):
    """A breaking release this app did not pass, and when; it is tried again a day later, not on every check."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    release: int
    tried_at: dt.datetime
    failed: tuple[str, ...]


def _read_tried(root: Path) -> _Tried | None:
    path = root / UPGRADE_RECORD
    try:
        return _Tried.model_validate_json(path.read_bytes()) if path.is_file() else None
    except ValidationError:
        return None


def _snapshot(root: Path, paths: set[str]) -> dict[str, bytes | None]:
    return {path: (root / path).read_bytes() if (root / path).is_file() else None for path in sorted(paths)}


def _restore(root: Path, saved: dict[str, bytes | None]) -> None:
    for path, data in saved.items():
        target = root / path
        if data is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def _stay_note(base: m.PlatformRelease, chosen: m.PlatformRelease, failed: tuple[str, ...]) -> str:
    deadline = f"by {chosen.deadline:%d %B %Y}, when it is applied anyway" if chosen.deadline is not None else "before it is required"
    return (
        f"Platform release {chosen.release} changes what the checks refuse, and this app does not pass them yet ({', '.join(failed)}), "
        f"so it stays on platform release {base.release}. Run `ctzd sync`, then `ctzd check`, and fix what it reports {deadline}."
    )


def _pending_breaking(target: Target, now: dt.datetime) -> tuple[m.PlatformRelease, m.PlatformRelease] | None:
    """The recorded release and the channel's, when the channel's is breaking for this app and not yet past its deadline."""
    recorded = sync.recorded_release(target.root)
    if recorded is None:
        return None
    try:
        chosen = _local_release(target, recorded)
        base = require_usable(target.releases.get(recorded))
    except CliError:
        return None
    if chosen.release == base.release or not is_breaking(target.platform, base, chosen, target.app.app.ui):
        return None
    # Past its deadline a breaking release applies like any other.
    return None if chosen.deadline is not None and now.date() >= chosen.deadline else (base, chosen)


@dataclass(frozen=True)
class _Stay:
    """The app stays on `base` for now: `chosen`, the channel's release, is breaking for it."""

    base: m.PlatformRelease
    chosen: m.PlatformRelease


def _breaking_move(target: Target, *, fast: bool, now: dt.datetime) -> _Stay | list[Step] | None:
    """Nothing breaking pending (None); the steps of a breaking release the app passed and now has; or the app stays."""
    pending = _pending_breaking(target, now)
    if pending is None:
        return None
    base, chosen = pending
    tried = _read_tried(target.root)
    recently = tried is not None and tried.release == chosen.release and now - tried.tried_at < _RETRY_BREAKING
    # A check without tests cannot tell whether the app passes, so it stays; so does one that failed it within a day.
    if fast or recently:
        return _Stay(base, chosen)
    steps = _try_release(target, base, chosen, now)
    return _Stay(base, chosen) if steps is None else steps


def _try_release(target: Target, base: m.PlatformRelease, chosen: m.PlatformRelease, now: dt.datetime) -> list[Step] | None:
    """Run every check on `chosen` and return them when they pass; else put the build files back, record it, and return None."""
    ui = target.app.app.ui
    touched = {*assets.locked_files(base, ui), *assets.locked_files(chosen, ui), sync.VERSION_FILE, "pyproject.toml", "uv.lock", *(sync.WEB_FILES if ui == "react" else ())}
    saved = _snapshot(target.root, touched)
    steps = _run(target, "local", fast=False, first=platform_files_at(target, chosen))
    failed = tuple(step.name for step in steps if step.state == "failed")
    if not failed:
        (target.root / UPGRADE_RECORD).unlink(missing_ok=True)
        return steps
    _restore(target.root, saved)
    record = target.root / UPGRADE_RECORD
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(_Tried(release=chosen.release, tried_at=now, failed=failed).model_dump_json(), encoding="utf-8")
    return None


def run_all(target: Target, *, mode: Mode, fast: bool, candidate: m.PlatformRelease | None = None, now: dt.datetime | None = None) -> list[Step]:
    """Every gate. Locally, a breaking release of the channel is tried first and kept only when the app passes on it;
    `candidate` forces the move to that release, as the platform's check of a release before it is promoted does."""
    if mode == "ci":
        return _run(target, mode, fast=fast, first=platform_files(target, mode))
    if candidate is not None:
        return _run(target, mode, fast=fast, first=platform_files_at(target, candidate))
    moment = now or dt.datetime.now(dt.UTC)
    staying = _breaking_move(target, fast=fast, now=moment)
    if staying is None:
        return _run(target, mode, fast=fast, first=platform_files(target, mode))
    if not isinstance(staying, _Stay):
        return staying
    base, chosen = staying.base, staying.chosen
    first = platform_files_at(target, base)
    tried = _read_tried(target.root)
    failed = tried.failed if tried is not None and tried.release == chosen.release else ("not tried yet: run `ctzd check` without --fast",)
    note = _stay_note(base, chosen, failed)
    first = Step(first.name, first.state, "\n".join(part for part in (first.detail, note) if part), paths=first.paths, warning=note)
    return _run(target, mode, fast=fast, first=first)


def _run(target: Target, mode: Mode, *, fast: bool, first: Step) -> list[Step]:
    root = target.root
    steps = [first]
    if steps[0].state == "failed" and mode == "ci":
        return steps
    steps.append(environment(root, mode))
    if steps[-1].state == "failed":
        return steps
    react = target.app.app.ui == "react"
    if react:
        steps.append(web_install(root, mode))
        if steps[-1].state == "failed":
            return steps
        steps.append(client(root, mode))
    steps += [formatting(root, mode), lint(root, mode), types(root), layers(root), sql_files(root), numbers(root)]
    if react:
        steps += [web_check(root, mode), web_build(root), sample_pages(root, mode)]
    steps.append(Step("tests", "skipped", "--fast") if fast else tests(root))
    return steps
