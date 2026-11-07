"""`ctzd setup`: make sure the laptop has what ctzd needs, installing a private git when it is missing."""

import io
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from ctzd_cli import auth, config, proc
from ctzd_cli.errors import CliError

# The first Claude Code release whose docs say it reads AGENTS.md without a CLAUDE.md.
CLAUDE_MIN_VERSION = (2, 1, 277)
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_CLAUDE_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")


@dataclass(frozen=True)
class Check:
    name: str
    state: Literal["ok", "fixed", "warning", "missing"]
    detail: str


def mingit_dir() -> Path:
    return config.user_dir() / "mingit"


def install_mingit(*, client: httpx.Client) -> Path:
    """Download and unpack MinGit, Git for Windows' portable build, into the user's profile."""
    url = config.load().platform.mingit_url
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    target = mingit_dir()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for member in archive.namelist():
            if member.startswith(("/", "\\")) or ".." in Path(member).parts:
                msg = f"The MinGit download contains an unsafe path: {member}"
                raise CliError(msg)
        archive.extractall(target)
    return target / "cmd" / "git.exe"


def check_git(*, client: httpx.Client) -> Check:
    found = shutil.which("git")
    if found is not None:
        return Check("git", "ok", found)
    private = mingit_dir() / "cmd" / "git.exe"
    if private.is_file():
        return Check("git", "ok", str(private))
    if os.name != "nt":
        return Check("git", "missing", "Install git with the system package manager.")
    return Check("git", "fixed", f"Installed a private git at {install_mingit(client=client)}")


def check_login(*, client: httpx.Client) -> Check:
    token = auth.token()
    if token is None:
        return Check("login", "missing", "Run `ctzd login --start`, show the person the code, then `ctzd login --wait`.")
    try:
        profile = auth.fetch_profile(token, client=client)
    except CliError as err:
        return Check("login", "missing", err.hint or err.message)
    return Check("login", "ok", f"Logged in to GitHub as {profile.login}")


def claude_version(output: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(output)
    return None if match is None else (int(match[1]), int(match[2]), int(match[3]))


def check_claude(cwd: Path) -> Check:
    executable = shutil.which("claude")
    if executable is None:
        return Check("claude code", "ok", "Claude Code is not installed; nothing to check.")
    version = claude_version(proc.run([executable, "--version"], cwd=cwd, timeout=30).output)
    if version is not None and version < CLAUDE_MIN_VERSION:
        wanted = ".".join(map(str, CLAUDE_MIN_VERSION))
        return Check("claude code", "warning", f"Claude Code {'.'.join(map(str, version))} may not read AGENTS.md; update it to {wanted} or later.")
    return Check("claude code", "ok", "Claude Code reads AGENTS.md.")


def user_memory_files() -> set[Path]:
    """The person's own CLAUDE.md, which Claude Code reads as user memory and does not count against AGENTS.md."""
    found = {Path.home() / ".claude" / "CLAUDE.md"}
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if config_dir:
        found.add(Path(config_dir) / "CLAUDE.md")
    return {path.resolve() for path in found}


def blocking_claude_files(cwd: Path) -> list[Path]:
    """CLAUDE.md files at or above `cwd`; any of them makes Claude Code ignore AGENTS.md."""
    personal = user_memory_files()
    found: list[Path] = []
    for folder in (cwd, *cwd.parents):
        found += [folder / name for name in _CLAUDE_FILES if (folder / name).is_file() and (folder / name).resolve() not in personal]
    return found


def check_claude_files(cwd: Path) -> Check:
    blocking = blocking_claude_files(cwd)
    if not blocking:
        return Check("agent instructions", "ok", "Nothing stops agents from reading AGENTS.md.")
    listed = ", ".join(str(path) for path in blocking)
    return Check("agent instructions", "warning", f"Claude Code will read {listed} instead of the app's AGENTS.md. Rename or remove it.")


def run(cwd: Path, *, client: httpx.Client) -> list[Check]:
    return [Check("uv", "ok", proc.which("uv", hint="Install uv first.")), check_git(client=client), check_login(client=client), check_claude(cwd), check_claude_files(cwd)]
