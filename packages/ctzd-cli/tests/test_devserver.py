import json
import socket
import sys
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


def _http_server(port: int) -> list[str]:
    return [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]


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
    command = [sys.executable, "-c", "import sys; print('No module named streamlit'); sys.exit(3)"]
    failed = devserver.start(tmp_path, command, port=_free(), env={})
    assert failed == devserver.Failed(reason="stopped right away (exit 3)", log="No module named streamlit")
    assert not (tmp_path / devserver.STATE).exists()


def test_a_taken_port_is_skipped(taken_port: int) -> None:
    assert not devserver.port_free(taken_port)
    found = devserver.free_port(taken_port)
    assert found is not None
    assert found != taken_port


def test_a_record_of_a_server_that_is_gone_is_dropped_without_killing_anything(tmp_path: Path) -> None:
    (tmp_path / devserver.STATE).parent.mkdir()
    (tmp_path / devserver.STATE).write_text(json.dumps({"pid": 1, "port": _free()}))
    assert devserver.stop(tmp_path) is None
    assert not (tmp_path / devserver.STATE).exists()


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
