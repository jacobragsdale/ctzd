"""`@ctzd.cached`: an in-process TTL cache for loaders, identical in datasets and jobs."""

import functools
import re
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from datetime import timedelta

import pandas as pd

from ctzd._errors import CtzdError

_DURATION = re.compile(r"(?P<amount>\d+)\s*(?P<unit>[smhd])")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_caches: weakref.WeakSet[_Cache] = weakref.WeakSet()


def parse_ttl(ttl: str | int | timedelta) -> float:
    """Seconds from `"15m"`, `"2h"`, `"30s"`, `"1d"`, an int of seconds, or a timedelta."""
    if isinstance(ttl, timedelta):
        seconds = ttl.total_seconds()
    elif isinstance(ttl, int):
        seconds = float(ttl)
    else:
        match = _DURATION.fullmatch(ttl.strip())
        if match is None:
            msg = f"Invalid ttl {ttl!r}: use a number followed by s, m, h, or d, such as '15m'."
            raise CtzdError(msg)
        seconds = float(int(match["amount"]) * _UNIT_SECONDS[match["unit"]])
    if seconds <= 0:
        msg = "ttl must be positive."
        raise CtzdError(msg)
    return seconds


class _Cache:
    def __init__(self, ttl_seconds: float, max_entries: int, clock: Callable[[], float]) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self.entries: OrderedDict[object, tuple[float, object]] = OrderedDict()
        self.lock = threading.Lock()

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()


def clear_all() -> None:
    """Empty every cache in the process; `ctzd.testing` calls this around each fake."""
    for cache in list(_caches):
        cache.clear()


def cached[**P, R](*, ttl: str | int | timedelta, max_entries: int = 64, clock: Callable[[], float] = time.monotonic) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Cache a function's result per distinct arguments for `ttl`.

    Arguments must be hashable: strings, numbers, dates, and tuples of those.
    A cached DataFrame is returned as a shallow copy, so callers can change
    their copy without changing the cache.
    """
    cache = _Cache(parse_ttl(ttl), max_entries, clock)
    _caches.add(cache)

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            key = (args, tuple(sorted(kwargs.items())))
            try:
                hash(key)
            except TypeError as err:
                name = getattr(function, "__qualname__", "A cached function")
                msg = f"{name} is cached, so its arguments must be hashable (str, int, date, tuple)."
                raise CtzdError(msg) from err
            now = cache.clock()
            with cache.lock:
                hit = cache.entries.get(key)
                if hit is not None and hit[0] > now:
                    cache.entries.move_to_end(key)
                    return _fresh(hit[1])  # ty: ignore[invalid-return-type] - entries only ever hold this function's results.
            value = function(*args, **kwargs)
            with cache.lock:
                cache.entries[key] = (now + cache.ttl_seconds, value)
                cache.entries.move_to_end(key)
                while len(cache.entries) > cache.max_entries:
                    cache.entries.popitem(last=False)
            return _fresh(value)

        return wrapper

    return decorate


def _fresh[T](value: T) -> T:
    if isinstance(value, pd.DataFrame):
        return value.copy(deep=False)
    return value
