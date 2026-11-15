"""`python -m app`: serve the dashboard with uvicorn, on Dash's FastAPI backend, with its health and readiness routes.
Platform-owned: ctzd sync rewrites this file.

    python -m app --host 0.0.0.0 --port 8080    in the pod
    python -m app --dev --port 8501             on the desktop, through `ctzd dev`: reloads when the code changes

Under `--dev` this process watches `src/` and runs the server in a child process, which it replaces after each change.
uvicorn's own reloader is not used: on Windows it stops its child with a console Ctrl+C, which a server that `ctzd dev`
starts in the background never receives, so it went on serving the old code. This process holds the port's socket and
hands it to each server, so requests made during a restart wait instead of failing. While the code cannot start (a
syntax error, say), a standby server answers on it: `/health/ready` is 503 with the reason, `/_ctzd/dev` still answers
so `ctzd dev --stop` finds the server, and pages say the app is being fixed and load again once it starts.
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import override
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer
from wsgiref.types import StartResponse, WSGIEnvironment

import uvicorn
import watchfiles
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

DEV_ID = "CTZD_DEV_ID"
"""The token `ctzd dev` gives the server, which it answers at `/_ctzd/dev`, so `ctzd dev --stop` stops only its own."""
_DEV = "CTZD_DEV"
_BOOT = "/_ctzd/boot"
_WATCHED = "src"
_STANDBY_BOOT = "standby"
_STOP_SECONDS = 10
_FROM_STDIN = "stdin"  # on Windows the socket comes through the child's stdin, from socket.share
# Under `ctzd dev`, every open page asks each second which server process answers it, and loads itself again when a
# new one does: uvicorn starts a new process for each change to the code, and Dash's own hot reload, on the FastAPI
# backend, sees the new process but does not redraw the page.
_RELOAD_SCRIPT = (
    "<script>(() => { let boot = null; const ask = () => fetch('" + _BOOT + "', {cache: 'no-store'})"
    ".then(answer => answer.ok ? answer.text() : null)"
    ".then(now => { if (now && boot && now !== boot) { window.location.reload(); } if (now) { boot = now; } })"
    ".catch(() => null).finally(() => setTimeout(ask, 1000)); ask(); })();</script>"
)


def _dash_variables() -> list[str]:
    """Variables that would change what Dash does behind the app's back, its dev tools in production among them."""
    return sorted(name for name in os.environ if name.startswith("DASH_") or name == "_DASH_FASTAPI_CONFIG")


def server() -> FastAPI:
    """The app's server: Dash's FastAPI app, with `/health/live`, `/health/ready` and, under `ctzd dev`, `/_ctzd/dev`
    and `/_ctzd/boot`, the process's id, by which open pages load themselves again after a change to the code."""
    from app.app import app  # noqa: PLC0415 - built when the server starts, in uvicorn's worker under --dev

    built = app.server
    if not isinstance(built, FastAPI):
        msg = "The app is not on Dash's FastAPI backend: build it with ui.dash_app(TITLE) in app.py."
        raise TypeError(msg)
    dev_mode = os.environ.get(_DEV) == "1"
    if dev_mode:
        # Dash's checks of component properties, without its debug panel, which would sit over the page the person
        # is looking at: errors reach them through `ui.on_error`, and the agent through `ctzd render`.
        app.enable_dev_tools(debug=True, dev_tools_ui=False, dev_tools_hot_reload=False, dev_tools_disable_version_check=True)
        app.index_string = app.index_string.replace("{%renderer%}", "{%renderer%}\n" + _RELOAD_SCRIPT)
    token = os.environ.get(DEV_ID, "")
    boot = uuid.uuid4().hex

    def live() -> PlainTextResponse:
        return PlainTextResponse("ok")

    def dev() -> PlainTextResponse:
        return PlainTextResponse(token)

    def booted() -> PlainTextResponse:
        return PlainTextResponse(boot)

    routes = [("/health/live", live), ("/health/ready", live), *([("/_ctzd/dev", dev)] if token else []), *([(_BOOT, booted)] if dev_mode else [])]
    # Added before Dash's first request sets up its own routes, so they come before its catch-all page route.
    for path, endpoint in routes:
        built.add_api_route(path, endpoint, methods=["GET"], include_in_schema=False)
    return built


class _StandbyServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    @override
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Not each request: the pages' checks each second would bury the error in the log."""


class _Standby:
    """Answers on the port while the app cannot start, so the server stays findable and says why it is not ready."""

    def __init__(self, listener: socket.socket, port: int, reason: str) -> None:
        token = os.environ.get(DEV_ID, "")
        page = f"<!DOCTYPE html><html><head><title>Starting again</title></head><body><p>{reason}</p>{_RELOAD_SCRIPT}</body></html>"
        plain = {"/_ctzd/dev": ("200 OK", token), "/health/live": ("200 OK", "ok"), _BOOT: ("200 OK", _STANDBY_BOOT), "/health/ready": ("503 Service Unavailable", reason)}

        def answer(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
            found = plain.get(str(environ.get("PATH_INFO", "")))
            status, text, kind = ("503 Service Unavailable", page, "text/html") if found is None else (*found, "text/plain")
            start_response(status, [("Content-Type", f"{kind}; charset=utf-8"), ("Cache-Control", "no-store")])
            return [text.encode()]

        # On the supervisor's own socket, which stays open between runs of the app, so the port never refuses a request.
        self._server = _StandbyServer(("127.0.0.1", port), _QuietHandler, bind_and_activate=False)
        self._server.socket.close()
        self._server.socket = listener.dup()
        self._server.server_name, self._server.server_port = "localhost", port
        self._server.setup_environ()
        self._server.set_app(answer)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _start(listener: socket.socket) -> subprocess.Popen[bytes]:
    """The server, in a child process that serves on `listener` and writes to this one's output, which `ctzd dev` keeps
    in `.ctzd/dev.log`."""
    command = [sys.executable, "-m", "app", "--serve-dev"]
    if sys.platform == "win32":
        child = subprocess.Popen([*command, _FROM_STDIN], stdin=subprocess.PIPE)
        if child.stdin is not None:
            child.stdin.write(listener.share(child.pid))
            child.stdin.close()
        return child
    return subprocess.Popen([*command, str(listener.fileno())], pass_fds=(listener.fileno(),))


def _serve_dev(handoff: str) -> int:
    """The server `--dev` runs, on the socket its supervisor hands it."""
    if sys.platform == "win32":
        listener = socket.fromshare(sys.stdin.buffer.read())
    else:
        listener = socket.socket(fileno=int(handoff))
    # A selector loop, as uvicorn's own workers use: on Windows the default proactor loop ties the socket to its
    # completion port, which no later server could then accept on (WinError 87).
    config = uvicorn.Config("app.__main__:server", factory=True, loop="asyncio:SelectorEventLoop", log_level="info", timeout_graceful_shutdown=2)
    uvicorn.Server(config).run(sockets=[listener])
    return 0


def _stop(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    taskkill = shutil.which("taskkill") if sys.platform == "win32" else None
    if taskkill is not None:
        # The whole tree: a virtual environment's python.exe is a launcher that starts the real interpreter.
        subprocess.run([taskkill, "/PID", str(child.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        child.terminate()
    try:
        child.wait(timeout=_STOP_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def _say(message: str) -> None:
    sys.stderr.write(f"ctzd dev: {message}\n")
    sys.stderr.flush()


def _supervise(host: str, port: int) -> int:
    """Run the server, and start it again after each change under `src/`; between a crash and the next change, stand by."""
    try:
        listener = socket.create_server((host, port))
    except OSError as err:
        _say(f"cannot listen on {host}:{port}: {err}")
        return 1
    child: subprocess.Popen[bytes] | None = _start(listener)
    standby: _Standby | None = None
    try:
        # Each second with no change is a chance to see that the server stopped.
        for changes in watchfiles.watch(_WATCHED, rust_timeout=1000, yield_on_timeout=True):
            if changes:
                changed = sorted({os.path.relpath(path) for _change, path in changes})
                _say(f"{', '.join(changed)} changed: starting the app again.")
                if child is not None:
                    _stop(child)
                if standby is not None:
                    standby.close()
                    standby = None
                child = _start(listener)
            elif child is not None and (code := child.poll()) is not None:
                child = None
                reason = f"The app stopped (exit {code}) with the error above in .ctzd/dev.log. It starts again when a file under {_WATCHED}/ changes."
                _say(reason)
                standby = _Standby(listener, port, reason)
    except KeyboardInterrupt:
        pass
    finally:
        if child is not None:
            _stop(child)
        if standby is not None:
            standby.close()
        listener.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dev", action="store_true", help="reload when the code changes, with Dash's dev tools")
    parser.add_argument("--serve-dev", metavar="SOCKET", help=argparse.SUPPRESS)  # the server that --dev runs, and its socket
    args = parser.parse_args(argv)
    if found := _dash_variables():
        sys.stderr.write(f"Refusing to start: {', '.join(found)} would change how Dash runs this app. Unset them.\n")
        return 2
    if args.dev:
        if not Path(_WATCHED).is_dir():
            sys.stderr.write(f"Run --dev from the app's folder, which holds {_WATCHED}/.\n")
            return 2
        os.environ[_DEV] = "1"
        return _supervise(args.host, args.port)
    if args.serve_dev is not None:
        return _serve_dev(args.serve_dev)
    uvicorn.run(server(), host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*", log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
