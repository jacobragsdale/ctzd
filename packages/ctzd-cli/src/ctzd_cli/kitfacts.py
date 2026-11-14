"""What an app's installed kit says about itself: its sources and loaders, the packages that do I/O, and the
department's skill. `ctzd_kit.explore describe` prints it inside the app's environment; it is kept in `.ctzd/kit.json`
until `uv.lock` changes, so every command does not start Python to ask again."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ctzd_cli import proc
from ctzd_cli.errors import CliError

CACHE = ".ctzd/kit.json"
_TIMEOUT = 300.0


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Column(_Model):
    name: str
    type: str


class LoaderFacts(_Model):
    name: str
    signature: str
    doc: str
    columns: tuple[Column, ...]
    example: str


class SourceFacts(_Model):
    name: str
    kind: str
    description: str
    refreshed: str | None
    dialect: str | None
    desktop: bool
    io: tuple[str, ...]
    credentials: bool
    serves_sample: str | None
    loaders: tuple[LoaderFacts, ...]


class KitFacts(_Model):
    kit: str
    sbu: str
    title: str
    sources: tuple[SourceFacts, ...]
    io: tuple[str, ...]
    skill: str

    def source(self, name: str) -> SourceFacts | None:
        return next((source for source in self.sources if source.name == name), None)


class _Cached(_Model):
    lock: str
    facts: KitFacts


def _lock_hash(root: Path) -> str:
    path = root / "uv.lock"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def run_explore(root: Path, *args: str, env: dict[str, str] | None = None) -> dict[str, object]:
    """`python -m ctzd_kit.explore ARGS` in the app's environment, its one line of JSON."""
    uv = proc.which("uv", hint="Install uv first.")
    env_file = ["--env-file", ".env"] if (root / ".env").is_file() else []
    result = proc.run([uv, "run", "--no-sync", *env_file, "python", "-m", "ctzd_kit.explore", *args], cwd=root, env=env, timeout=_TIMEOUT)
    last = result.output.strip().splitlines()[-1] if result.output.strip() else ""
    try:
        found: object = json.loads(last) if last.startswith("{") else None
    except ValueError:
        found = None
    if not isinstance(found, dict):
        msg = "The app's kit could not be asked about itself."
        raise CliError(msg, hint=proc.tail(result.output, 20) + "\nRun `ctzd check`, which installs the app's packages, then try again.")
    answer = {str(key): value for key, value in found.items()}
    if "error" in answer:
        raise CliError(str(answer["error"]))
    return answer


def facts(root: Path, *, mode: Literal["cached", "fresh"] = "cached") -> KitFacts | None:
    """The kit's facts, or None while the app's packages are not installed."""
    if not (root / ".venv").is_dir():
        return None
    lock = _lock_hash(root)
    path = root / CACHE
    if mode == "cached" and path.is_file():
        try:
            cached = _Cached.model_validate_json(path.read_bytes())
        except ValidationError:
            cached = None
        if cached is not None and cached.lock == lock:
            return cached.facts
    found = KitFacts.model_validate(run_explore(root, "describe"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_Cached(lock=lock, facts=found).model_dump_json(), encoding="utf-8")
    return found


def tokens(found: KitFacts) -> dict[str, str]:
    """What the locked files that come from the kit fill in: the packages its sources list as doing I/O."""
    return {"io_modules": "".join(f"\n    {package}" for package in found.io)}


def skill_files(found: KitFacts) -> dict[str, str]:
    path = f"skills/sbu-{found.sbu}/SKILL.md"
    return {f".agents/{path}": found.skill, f".claude/{path}": found.skill}
