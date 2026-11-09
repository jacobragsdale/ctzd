"""Shared pieces of the validation stages: a work folder, local wheels, a platform config, and running ctzd."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CTZD = REPO / ".venv" / "bin" / "ctzd"
PLATFORM_TOML = REPO / "packages" / "ctzd-cli" / "src" / "ctzd_cli" / "platform.toml"
RELEASE_TOML = REPO / "packages" / "ctzd-cli" / "src" / "ctzd_cli" / "assets" / "release.toml"


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        msg = f"{name} is not installed"
        raise SystemExit(msg)
    return found


def build_wheels(target: Path) -> Path:
    """ctzd-core and ctzd-fi as local wheels, so generated apps can install them."""
    target.mkdir(parents=True, exist_ok=True)
    for package in ("ctzd-core", "ctzd-fi"):
        subprocess.run([tool("uv"), "build", "--quiet", "--package", package, "--out-dir", str(target)], cwd=REPO, check=True)
    return target


def npm_packages(base: Path, url: str) -> dict[str, str]:
    """The firm's npm packages, copied into `base/npm` and served from `url`, by package: replacements for the bundled
    release's `file:` paths, which point into the operator's home. SHIM (MVP), until a registry serves them.

    A URL, not a `file:` path, because pnpm writes a tarball's path into the lockfile relative to the app, and
    the build's workspace is not where the person's app is.
    """
    target = base / "npm"
    target.mkdir(parents=True, exist_ok=True)
    found: dict[str, str] = {}
    for package, spec in tomllib.loads(RELEASE_TOML.read_text())["npm"]["packages"].items():
        if not spec.startswith("file:"):
            continue
        source = Path(spec.removeprefix("file:")).expanduser()
        copied = target / source.name
        if not copied.is_file():
            # A copy under a name of its own first, so trials starting together never read a half-written file.
            partial = target / f".{source.name}.{os.getpid()}"
            shutil.copyfile(source, partial)
            partial.replace(copied)
        found[package] = f"{url}/{source.name}"
    return found


def platform_config(work: Path, wheels: Path, *, api_url: str, extra: dict[str, str] | None = None, npm: dict[str, str] | None = None) -> Path:
    """The bundled platform.toml with other values overridden, and release overrides: local wheels plus PyPI for the
    release's internal index, and `npm` for its npm packages."""
    text = PLATFORM_TOML.read_text().replace('api_url = "https://api.ctzd.contoso.com"', f'api_url = "{api_url}"')
    for old, new in (extra or {}).items():
        text = text.replace(old, new)
    text += f"""
[release_overrides]
python_index = [{{name = "local", url = "{wheels}", format = "flat"}}, {{name = "pypi", url = "https://pypi.org/simple", default = true}}]
"""
    if npm is not None and npm:
        text += "npm_packages = {" + ", ".join(f'"{package}" = "{spec}"' for package, spec in npm.items()) + "}\n"
    path = work / "platform.toml"
    path.write_text(text)
    return path


@dataclass
class Result:
    argv: list[str]
    code: int
    body: dict[str, object]
    raw: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def text(self, key: str) -> str:
        return str(self.body.get(key, ""))

    def next_steps(self) -> list[str]:
        found = self.body.get("next")
        return [str(step) for step in found] if isinstance(found, list) else []


@dataclass
class Report:
    """Named expectations; a failed one is recorded with what was seen, and the run carries on."""

    stage: str
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def expect(self, name: str, *, ok: bool, seen: object = None) -> bool:
        if ok:
            self.passed.append(name)
            sys.stdout.write(f"  ok    {name}\n")
        else:
            detail = "" if seen is None else f" -- saw: {str(seen)[:600]}"
            self.failed.append(f"{name}{detail}")
            sys.stdout.write(f"  FAIL  {name}{detail}\n")
        return ok

    def finish(self) -> int:
        sys.stdout.write(f"{self.stage}: {len(self.passed)} passed, {len(self.failed)} failed\n")
        return 1 if self.failed else 0


def run_ctzd(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 600) -> Result:
    full = [str(CTZD), *argv, "--json"]
    merged = {key: value for key, value in os.environ.items() if not key.startswith("CTZD_")} | env
    completed = subprocess.run(full, cwd=cwd, env=merged, capture_output=True, text=True, timeout=timeout, check=False)
    try:
        parsed: object = json.loads(completed.stdout)
    except json.JSONDecodeError:
        parsed = {}
    body = {str(key): value for key, value in parsed.items()} if isinstance(parsed, dict) else {}
    return Result(argv=argv, code=completed.returncode, body=body, raw=completed.stdout + completed.stderr)
