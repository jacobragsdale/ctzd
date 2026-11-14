"""Shared pieces of the validation stages: a work folder, local wheels, a platform config, and running ctzd."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CTZD = REPO / ".venv" / "bin" / "ctzd"
PLATFORM_TOML = REPO / "packages" / "ctzd-cli" / "src" / "ctzd_cli" / "platform.toml"


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        msg = f"{name} is not installed"
        raise SystemExit(msg)
    return found


WHEELS = ("ctzd-kit", "ctzd-common", "ctzd-fi", "ctzd-mas", "fi-risklib")
"""The packages an app installs from this repository, built as local wheels."""


def build_wheels(target: Path) -> Path:
    """The kits and what they build on, as local wheels, so apps made offline can install them."""
    target.mkdir(parents=True, exist_ok=True)
    for package in WHEELS:
        subprocess.run([tool("uv"), "build", "--quiet", "--package", package, "--out-dir", str(target)], cwd=REPO, check=True)
    return target


def remove_sample_marks(app: Path) -> None:
    """Take the template's sample marks out, as an agent does once it has built what the person asked for, so the app ships."""
    for path in [*(app / "src" / "app" / "pages").glob("*.py"), *(app / "src" / "app" / "jobs").glob("*.py")]:
        first, _, rest = path.read_text().partition("\n")
        if "ctzd: sample" in first:
            path.write_text(rest)


def platform_config(work: Path, wheels: Path, *, extra: dict[str, str] | None = None) -> Path:
    """The bundled platform.toml with other values overridden, and build overrides: local wheels plus PyPI for the
    internal index."""
    text = PLATFORM_TOML.read_text()
    for old, new in (extra or {}).items():
        text = text.replace(old, new)
    text += f"""
[build_overrides]
python_index = [{{name = "local", url = "{wheels}", format = "flat"}}, {{name = "pypi", url = "https://pypi.org/simple", default = true}}]
"""
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
