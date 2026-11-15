import json
import os
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from ctzd_cli import devserver, main

from .conftest import make_app, run


@pytest.fixture
def taken_port() -> Iterator[int]:
    """A port another program is listening on."""
    port = _free()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen()
        yield port


_SERVER = """
import http.server, os, sys, time
time.sleep(float(sys.argv[3]))
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = os.environ.get("CTZD_DEV_ID", "").encode() if self.path == "/_ctzd/dev" else b"page"
        self.send_response(503 if self.path == "/health/ready" and sys.argv[2] == "standby" else 200)
        self.end_headers()
        self.wfile.write(body)
print("SyntaxError: invalid syntax" if sys.argv[2] == "standby" else "serving", flush=True)
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""


def _http_server(port: int, *, role: str = "app", after: float = 0.0) -> list[str]:
    """A server that reports its ctzd dev token, as the app's `python -m app --dev` does: as the app itself, or as its
    standby while the app's code cannot start, after `after` seconds."""
    return [sys.executable, "-c", _SERVER, str(port), role, str(after)]


def _free() -> int:
    port = devserver.free_port(devserver.FIRST_PORT + 100)
    assert port is not None
    return port


def test_a_started_server_answers_until_it_is_stopped(tmp_path: Path) -> None:
    port = _free()
    started = devserver.start(tmp_path, _http_server(port), port=port, env={})
    assert isinstance(started, devserver.Started)
    assert devserver.answering(port)
    assert devserver.recorded(tmp_path) == started.server

    assert devserver.stop(tmp_path) == started.server
    assert started.process.wait(timeout=10) is not None
    assert not devserver.answering(port)
    assert not (tmp_path / devserver.STATE).exists()


def test_a_server_that_exits_at_once_reports_its_output(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "import sys; print('No module named uvicorn'); sys.exit(3)"]
    failed = devserver.start(tmp_path, command, port=_free(), env={})
    assert failed == devserver.Failed(reason="stopped right away (exit 3)", log="No module named uvicorn")
    assert not (tmp_path / devserver.STATE).exists()


def test_a_taken_port_is_skipped(taken_port: int) -> None:
    assert not devserver.port_free(taken_port)
    found = devserver.free_port(taken_port)
    assert found is not None
    assert found != taken_port


def test_a_record_of_a_server_that_is_gone_is_dropped_without_killing_anything(tmp_path: Path) -> None:
    (tmp_path / devserver.STATE).parent.mkdir()
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    (tmp_path / devserver.STATE).write_text(json.dumps({"pid": gone.pid, "port": _free(), "token": "t"}))
    assert devserver.stop(tmp_path) is None
    assert not (tmp_path / devserver.STATE).exists()


def test_a_stale_record_never_stops_another_program_on_its_port(tmp_path: Path) -> None:
    port = _free()
    bystander = subprocess.Popen(_http_server(port), env={**os.environ, "CTZD_DEV_ID": "someone else's"})
    try:
        deadline = time.monotonic() + 10
        while not devserver.answering(port) and time.monotonic() < deadline:
            time.sleep(0.1)
        (tmp_path / devserver.STATE).parent.mkdir()
        (tmp_path / devserver.STATE).write_text(json.dumps({"pid": bystander.pid, "port": port, "token": "ours"}))
        assert devserver.stop(tmp_path) is None
        assert bystander.poll() is None
        assert not (tmp_path / devserver.STATE).exists()
    finally:
        bystander.kill()
        bystander.wait()


def test_a_server_whose_app_cannot_start_is_started_with_the_error(tmp_path: Path) -> None:
    port = _free()
    started = devserver.start(tmp_path, _http_server(port, role="standby"), port=port, env={})
    assert isinstance(started, devserver.Started)
    try:
        assert started.error is not None
        assert started.error.startswith("SyntaxError: invalid syntax")
        assert devserver.recorded(tmp_path) == started.server
    finally:
        devserver.stop(tmp_path)
        started.process.wait(timeout=10)


def test_a_server_between_two_runs_of_the_app_is_waited_for_not_forgotten(tmp_path: Path) -> None:
    port = _free()
    ours = secrets.token_hex(8)
    restarting = subprocess.Popen(_http_server(port, after=1.5), env={**os.environ, "CTZD_DEV_ID": ours})
    try:
        (tmp_path / devserver.STATE).parent.mkdir()
        (tmp_path / devserver.STATE).write_text(json.dumps({"pid": restarting.pid, "port": port, "token": ours}))
        found = devserver.recorded(tmp_path)
        assert found is not None
        assert (found.pid, found.port) == (restarting.pid, port)
    finally:
        restarting.kill()
        restarting.wait()


def test_dev_stop_with_nothing_running_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_app(tmp_path)
    code, out = run(["dev", "--stop"], tmp_path, capsys)
    assert code == 0
    assert out["summary"] == "No local dashboard was running."


def test_dev_on_a_taken_port_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str], taken_port: int) -> None:
    make_app(tmp_path)
    code, out = run(["dev", "--port", str(taken_port)], tmp_path, capsys)
    assert code == main.EXIT_USAGE
    assert out["summary"] == f"Port {taken_port} is in use by another program."
