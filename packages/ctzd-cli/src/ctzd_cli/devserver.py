"""`ctzd dev`: the local dashboard as a background process that `ctzd dev --stop` ends again.

The agent runs `ctzd dev` from a shell that returns, so the server runs in its own
session and its pid and port live in `.ctzd/dev.json`; stopping by that record means
the agent never has to find or kill processes itself.

A record goes stale (a new desktop session, ports handed out in another order), and
then another program may answer on its port. So the record holds a random token that
the server reports at `/_ctzd/dev`, and a process is stopped only when its port
answers with that token. While the app starts again after a change to its code, the
port answers late, or not at all: a record whose process is still alive is waited for,
so `ctzd dev` right after an edit stops the server instead of losing track of it.
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
_RESTART_SECONDS = 30.0  # how long the app may take to start again after a change, on a slow desktop
ID_VARIABLE = "CTZD_DEV_ID"  # the app's __main__.py reports it at IDENTITY_PATH
IDENTITY_PATH = "/_ctzd/dev"
LOST_PORT = "ctzd dev: cannot listen on"
"""What the app's `python -m app --dev` says when its port was taken between `ctzd dev` choosing it and the app
listening on it: two dashboards starting at the same moment."""
_PORT_RETRIES = 3


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
    error: str | None = None
    """When the server answers but the app's code does not start: the end of the log, with the error."""


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


def identity(port: int) -> str | None:
    """What the program on `port` answers at IDENTITY_PATH; None when nothing answers in time."""
    try:
        response = httpx.get(f"http://127.0.0.1:{port}{IDENTITY_PATH}", timeout=2.0)
    except httpx.HTTPError:
        return None
    return response.text.strip() if response.status_code == httpx.codes.OK else ""


def reports(port: int, token: str) -> bool:
    """Whether the program on `port` is the server that `ctzd dev` gave `token`."""
    found = identity(port)
    return found is not None and secrets.compare_digest(found, token)


def ready(port: int) -> bool:
    """Whether the app itself answers, not the standby that stands in while its code cannot start."""
    try:
        return httpx.get(f"http://127.0.0.1:{port}/health/ready", timeout=5.0).status_code == httpx.codes.OK
    except httpx.HTTPError:
        return False


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        tasklist = proc.which("tasklist", hint="tasklist comes with Windows.")
        found = subprocess.run([tasklist, "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True, check=False)
        return f'"{pid}"' in found.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _restarting(pid: int, port: int, token: str, *, clock: Callable[[], float]) -> bool:
    """Whether a server that did not answer in time is between two runs of the app: alive, and then answering with its
    token once the app has started again."""
    if not alive(pid):
        return False
    deadline = clock() + _RESTART_SECONDS
    while clock() < deadline and alive(pid):
        if reports(port, token):
            return True
        time.sleep(_POLL_SECONDS)
    return False


def _ours(pid: int, port: int, token: str, *, clock: Callable[[], float]) -> bool:
    found = identity(port)
    if found is not None:
        return secrets.compare_digest(found, token)  # another program that answers on the port is never waited for
    return _restarting(pid, port, token, clock=clock)


def recorded(root: Path, *, clock: Callable[[], float] = time.monotonic) -> Server | None:
    """The server `ctzd dev` started here, if it still answers on its port with its token."""
    path = root / STATE
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    match raw:
        case {"pid": int(pid), "port": int(port), "token": str(token)} if _ours(pid, port, token, clock=clock):
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
            return Started(server, process) if ready(port) else Started(server, process, error=_log_tail(root))
        code = process.poll()
        if code is not None:
            (root / STATE).unlink(missing_ok=True)
            return Failed(reason=f"stopped right away (exit {code})", log=_log_tail(root))
        time.sleep(_POLL_SECONDS)
    _kill(server)
    (root / STATE).unlink(missing_ok=True)
    return Failed(reason=f"did not answer within {_START_SECONDS:.0f} seconds", log=_log_tail(root))


def start_free(root: Path, command_for: Callable[[int], Sequence[str]], *, port: int, env: Mapping[str, str], clock: Callable[[], float] = time.monotonic) -> Started | Failed:
    """`start` on `port`, or on the next free port when another dashboard starting at the same moment took it."""
    started = start(root, command_for(port), port=port, env=env, clock=clock)
    for _ in range(_PORT_RETRIES):
        if not isinstance(started, Failed) or LOST_PORT not in started.log:
            return started
        found = free_port(port + 1)
        if found is None:
            return started
        port = found
        started = start(root, command_for(port), port=port, env=env, clock=clock)
    return started


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
