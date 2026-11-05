"""Process-wide test seams installed by `ctzd.testing`.

This is a module global rather than a ContextVar because Streamlit's AppTest
runs the page script in another thread, which would not see a ContextVar the
test had set.
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx
import pandas as pd

from ctzd._arrow import Storage
from ctzd._messages import SentMessage


@dataclass(frozen=True)
class StoreTarget:
    storage: Storage
    sbu: str
    app: str
    shared: Storage | None = None


@dataclass(frozen=True)
class SqlHandler:
    """A fake SQL result computed from the bind parameters."""

    function: Callable[[Mapping[str, object]], pd.DataFrame]


@dataclass(frozen=True)
class RestHandler:
    """A fake REST response computed from the request."""

    function: Callable[[httpx.Request], object]


type SqlFake = pd.DataFrame | SqlHandler


@dataclass(frozen=True)
class Overrides:
    sql: Mapping[str, Mapping[str, SqlFake]] | None = None
    rest: Mapping[str, Mapping[str, object]] | None = None
    store: StoreTarget | None = None
    outbox: list[SentMessage] | None = None


_lock = threading.Lock()
_current = Overrides()


def current() -> Overrides:
    with _lock:
        return _current


def install(overrides: Overrides) -> Overrides:
    """Replace the active overrides and return the previous ones."""
    global _current  # noqa: PLW0603 - the seam is process-wide by design; see the module docstring.
    with _lock:
        previous = _current
        _current = overrides
        return previous
