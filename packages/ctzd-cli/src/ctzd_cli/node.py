"""Node and pnpm, which a React app's pages build with: where ctzd keeps its own, and how it installs them.

A laptop has no admin rights, so `ctzd setup` puts Node in ctzd's own folder
(`node/`), from the zip the platform host serves or, failing that, from the
Node wheel through uv, and pnpm in `npm/`, npm's global prefix there, from the
internal npm registry. Every command that runs pnpm finds them through `env()`,
so a terminal opened before the installer changed PATH still works.

Windows is the target; none of the install path has run on Windows yet.
"""

import io
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from ctzd_cli import config
from ctzd_cli.errors import CliError

NODE_MAJOR = 26
PNPM_VERSION = "12.18.0"


def node_dir() -> Path:
    return config.user_dir() / "node"


def npm_dir() -> Path:
    """npm's global prefix for ctzd's own tools: pnpm lands here, in `bin/` outside Windows."""
    return config.user_dir() / "npm"


def path_dirs() -> list[Path]:
    """The folders that hold ctzd's own Node and pnpm, before anything else on PATH."""
    if os.name == "nt":
        return [node_dir(), npm_dir()]
    return [node_dir() / "bin", npm_dir() / "bin"]


def search_path() -> str:
    return os.pathsep.join([*(str(folder) for folder in path_dirs()), os.environ.get("PATH", "")])


def env() -> dict[str, str]:
    """The environment a child that runs Node or pnpm needs: PATH with ctzd's own folders first."""
    return {"PATH": search_path()}


def which(name: str) -> str | None:
    return shutil.which(name, path=search_path())


def major(version: str) -> int | None:
    """The major version of `v26.13.0` or `26.13.0`; None for anything else."""
    head = version.strip().removeprefix("v").split(".", 1)[0]
    return int(head) if head.isdigit() else None


def unpack_node(archive: bytes, target: Path) -> None:
    """Unpack Node's zip into `target`, dropping its top folder (`node-v26.13.0-win-x64/`), so `node.exe` is in `target`."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for member in zipped.infolist():
            parts = PurePosixPath(member.filename.replace("\\", "/")).parts
            if member.filename.startswith(("/", "\\")) or ".." in parts:
                msg = f"The Node download contains an unsafe path: {member.filename}"
                raise CliError(msg)
            inner = parts[1:]
            if not inner:
                continue
            destination = target.joinpath(*inner)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(zipped.read(member))
