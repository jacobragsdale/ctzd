"""No network: what the kit contract's check runs a kit's helpers and tests with, so code that quietly calls a service
fails there and must become a loader. `pytest -p ctzd_kit.pytest_nonetwork` runs tests so."""

import socket
from collections.abc import Generator
from contextlib import contextmanager


class NetworkBlockedError(RuntimeError):
    """Code reached for the network where the kit contract allows none."""


def _refuse(*_args: object, **_kwargs: object) -> object:
    msg = "This code reached for the network, which a kit allows only inside a source's loaders. Make the read a loader, with sample rows."
    raise NetworkBlockedError(msg)


@contextmanager
def blocked() -> Generator[None]:
    """No new connection and no name lookup for the block, but over a Unix socket."""
    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
    original_connect = socket.socket.connect

    def connect(self: socket.socket, address: str | tuple[object, ...]) -> None:
        if self.family == getattr(socket, "AF_UNIX", None):
            original_connect(self, address)
            return
        _refuse()

    socket.socket.connect = connect  # ty: ignore[invalid-assignment] - the patch is the point
    socket.socket.connect_ex = _refuse  # ty: ignore[invalid-assignment]
    socket.create_connection = _refuse  # ty: ignore[invalid-assignment]
    socket.getaddrinfo = _refuse  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved
