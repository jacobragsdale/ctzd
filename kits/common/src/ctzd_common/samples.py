"""Which sources served sample rows rather than real ones: the kit marks each one, a page's `ui.load` shows the
sample-data banner when its load saw one, and a job's table is written with the mark."""

import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_lock = threading.Lock()
_served: set[str] = set()
_watched: ContextVar[set[str] | None] = ContextVar("ctzd_common_samples", default=None)


def mark(source: str) -> None:
    """`source` served sample rows, in this process and in the block `watching` runs, where there is one."""
    with _lock:
        _served.add(source)
    watched = _watched.get()
    if watched is not None:
        watched.add(source)


def served() -> frozenset[str]:
    """Every source that served sample rows in this process."""
    with _lock:
        return frozenset(_served)


def reset() -> None:
    with _lock:
        _served.clear()


@contextmanager
def watching() -> Generator[set[str]]:
    """The sources that serve sample rows inside the block, worker threads anyio starts from it among them."""
    found: set[str] = set()
    token = _watched.set(found)
    try:
        yield found
    finally:
        _watched.reset(token)
        outer = _watched.get()
        if outer is not None:
            outer.update(found)
