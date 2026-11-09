"""`ctzd dev`: the local dashboard as a background process that `ctzd dev --stop` ends again.

The agent runs `ctzd dev` from a shell that returns, so the server runs in its own
session and its pid and port live in `.ctzd/dev.json`; stopping by that record means
the agent never has to find or kill processes itself.

A record goes stale (a new desktop session, ports handed out in another order), and
then another program may answer on its port. So the record holds a random token that
the server reports at `/_ctzd/dev`, and a process is stopped only when its port
answers with that token.
"""

import contextlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import httpx

from ctzd_cli import proc

STATE = Path(".ctzd") / "dev.json"
LOG = Path(".ctzd") / "dev.log"
FIRST_PORT = 8501
_PORTS_TRIED = 20
_START_SECONDS = 180.0  # The first `uv run` in a fresh app installs its packages.
_STOP_SECONDS = 10.0
_POLL_SECONDS = 0.5
ID_VARIABLE = "CTZD_DEV_ID"  # ctzd.api reports it at IDENTITY_PATH
IDENTITY_PATH = "/_ctzd/dev"


@dataclass(frozen=True)
class Server:
    pid: int
    port: int
    token: str

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"


@dataclass(frozen=True)
class Started:
    server: Server
    process: subprocess.Popen[bytes]


@dataclass(frozen=True)
class Failed:
    reason: str
    log: str


def answering(port: int) -> bool:
    with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
        return True
    return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return not answering(port)


def free_port(first: int = FIRST_PORT) -> int | None:
    return next((port for port in range(first, first + _PORTS_TRIED) if port_free(port)), None)


def reports(port: int, token: str) -> bool:
    """Whether the program on `port` is the server that `ctzd dev` gave `token`."""
    try:
        response = httpx.get(f"http://127.0.0.1:{port}{IDENTITY_PATH}", timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code == httpx.codes.OK and secrets.compare_digest(response.text.strip(), token)


def recorded(root: Path) -> Server | None:
    """The server `ctzd dev` started here, if it still answers on its port with its token."""
    path = root / STATE
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    match raw:
        case {"pid": int(pid), "port": int(port), "token": str(token)} if reports(port, token):
            return Server(pid=pid, port=port, token=token)
        case _:
            # Gone, or another program now answers on the port: forget it and kill nothing.
            path.unlink(missing_ok=True)
            return None


def start(root: Path, command: Sequence[str], *, port: int, env: Mapping[str, str], clock: Callable[[], float] = time.monotonic) -> Started | Failed:
    """Start `command`, which serves on `port`, and wait until it answers there or exits."""
    (root / LOG).parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    with (root / LOG).open("w", encoding="utf-8") as log:
        process = _spawn(command, cwd=root, env={**env, ID_VARIABLE: token}, log=log)
    server = Server(pid=process.pid, port=port, token=token)
    (root / STATE).write_text(json.dumps({"pid": server.pid, "port": server.port, "token": token}), encoding="utf-8")
    deadline = clock() + _START_SECONDS
    while clock() < deadline:
        if answering(port) and reports(port, token):
            return Started(server, process)
        code = process.poll()
        if code is not None:
            (root / STATE).unlink(missing_ok=True)
            return Failed(reason=f"stopped right away (exit {code})", log=_log_tail(root))
        time.sleep(_POLL_SECONDS)
    _kill(server)
    (root / STATE).unlink(missing_ok=True)
    return Failed(reason=f"did not answer within {_START_SECONDS:.0f} seconds", log=_log_tail(root))


def stop(root: Path) -> Server | None:
    """Stop the server `ctzd dev` started here; None when none was running."""
    server = recorded(root)
    if server is not None:
        _kill(server)
    (root / STATE).unlink(missing_ok=True)
    return server


def _spawn(command: Sequence[str], *, cwd: Path, env: Mapping[str, str], log: IO[str]) -> subprocess.Popen[bytes]:
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        return subprocess.Popen(list(command), cwd=cwd, env=proc.child_env(env), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    return subprocess.Popen(list(command), cwd=cwd, env=proc.child_env(env), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def _kill(server: Server) -> None:
    """End the server's whole process tree: `uv run` and the Python process it started."""
    if sys.platform == "win32":
        subprocess.run([proc.which("taskkill", hint="taskkill comes with Windows."), "/PID", str(server.pid), "/T", "/F"], capture_output=True, check=False)
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(server.pid, sig)
        deadline = time.monotonic() + _STOP_SECONDS
        while time.monotonic() < deadline and answering(server.port):
            time.sleep(_POLL_SECONDS)
        if not answering(server.port):
            return


def _log_tail(root: Path) -> str:
    try:
        return proc.tail((root / LOG).read_text(encoding="utf-8", errors="replace"), 30)
    except OSError:
        return ""
