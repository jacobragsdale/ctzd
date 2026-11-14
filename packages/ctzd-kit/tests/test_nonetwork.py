"""With the network blocked, no connection or name lookup gets out, a Unix socket still works, and afterwards
everything is as it was."""

import socket
from pathlib import Path

import pytest

from ctzd_kit import nonetwork, testing


def test_connections_and_lookups_are_refused_inside_the_block() -> None:
    with testing.blocked():
        with pytest.raises(nonetwork.NetworkBlockedError, match="only inside a source's loaders"):
            socket.create_connection(("127.0.0.1", 9))
        with pytest.raises(nonetwork.NetworkBlockedError):
            socket.getaddrinfo("db.contoso.com", 1521)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as raw:
            with pytest.raises(nonetwork.NetworkBlockedError):
                raw.connect(("127.0.0.1", 9))
            with pytest.raises(nonetwork.NetworkBlockedError):
                raw.connect_ex(("127.0.0.1", 9))


def test_everything_is_restored_after_the_block_even_when_it_fails() -> None:
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
    with pytest.raises(nonetwork.NetworkBlockedError), testing.blocked():
        socket.getaddrinfo("localhost", 80)
    assert (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo) == saved


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets only")
def test_a_unix_socket_still_connects(tmp_path: Path) -> None:
    path = str(tmp_path / "s")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        server.listen(1)
        with testing.blocked(), socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(path)
            accepted, _ = server.accept()
            accepted.close()
