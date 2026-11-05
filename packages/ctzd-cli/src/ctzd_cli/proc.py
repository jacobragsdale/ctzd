"""Run external programs (uv, git, the app's tools) with a clean environment."""

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ctzd_cli.errors import CliError

# Variables that would make a child git or uv act on the wrong repository or environment.
_LEAKY = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")


@dataclass(frozen=True)
class Completed:
    args: tuple[str, ...]
    code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def which(name: str, *, hint: str) -> str:
    path = shutil.which(name)
    if path is None:
        msg = f"`{name}` was not found."
        raise CliError(msg, hint=hint)
    return path


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _LEAKY}
    if extra is not None:
        env.update(extra)
    return env


def run(args: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None, timeout: float | None = None) -> Completed:
    try:
        result = subprocess.run(list(args), cwd=cwd, env=child_env(env), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as err:
        msg = f"`{' '.join(args[:3])}` did not finish within {timeout:.0f} seconds."
        raise CliError(msg) from err
    return Completed(args=tuple(args), code=result.returncode, output=(result.stdout or "") + (result.stderr or ""))


def tail(text: str, lines: int = 60) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])
