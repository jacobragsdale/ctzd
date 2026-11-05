"""Fakes for app tests: sources, the table store, and the outbox.

    with ctzd.testing.fake_sources(fi_pricing={"sql/prices.sql": prices_frame()}):
        ...

A SQL fake maps a SQL file to a DataFrame, or to `by_params(function)` to
compute the frame from the bind parameters. A REST fake maps `"GET /path"` to
a JSON payload, or to `by_request(function)` to compute it from the
`httpx.Request`. Inside `fake_sources`, any source that is not faked fails
loudly, so a test can never reach a real system.
"""

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx
import pandas as pd

from ctzd import _overrides
from ctzd._arrow import Storage
from ctzd._errors import SourceError
from ctzd._messages import SentMessage
from ctzd.cache import clear_all
from ctzd.sources import check_name

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def by_params(function: Callable[[Mapping[str, object]], pd.DataFrame]) -> _overrides.SqlHandler:
    """A SQL fake whose frame depends on the bind parameters."""
    return _overrides.SqlHandler(function)


def by_request(function: Callable[[httpx.Request], object]) -> _overrides.RestHandler:
    """A REST fake whose JSON payload depends on the request."""
    return _overrides.RestHandler(function)


def _is_rest_route(key: str) -> bool:
    return key.split(" ", 1)[0] in _METHODS


@contextmanager
def fake_sources(**sources: Mapping[str, object]) -> Generator[None]:
    sql: dict[str, Mapping[str, _overrides.SqlFake]] = {}
    rest: dict[str, Mapping[str, object]] = {}
    for name, routes in sources.items():
        check_name(name)
        kinds = {_is_rest_route(key) for key in routes}
        if kinds == {True}:
            rest[name] = dict(routes)
        elif kinds == {False}:
            sql[name] = {key: _sql_fake(name, key, value) for key, value in routes.items()}
        else:
            msg = f"Fake source {name!r} mixes REST routes and SQL files, or has no entries."
            raise SourceError(msg)
    previous = _overrides.install(replace(_overrides.current(), sql=sql, rest=rest))
    clear_all()
    try:
        yield
    finally:
        _overrides.install(previous)
        clear_all()


def _sql_fake(source: str, sql: str, value: object) -> _overrides.SqlFake:
    if isinstance(value, pd.DataFrame | _overrides.SqlHandler):
        return value
    msg = f"Fake for {source!r} {sql!r} must be a DataFrame or by_params(function)."
    raise SourceError(msg)


@contextmanager
def fake_store(root: Path, *, sbu: str = "test", app: str = "test") -> Generator[Path]:
    """Point `ctzd.write_table` and `ctzd.read_table` at a local folder, usually pytest's `tmp_path`."""
    root.mkdir(parents=True, exist_ok=True)
    target = _overrides.StoreTarget(storage=Storage.local(root.resolve().as_posix()), sbu=sbu, app=app)
    previous = _overrides.install(replace(_overrides.current(), store=target))
    try:
        yield root
    finally:
        _overrides.install(previous)


@contextmanager
def outbox() -> Generator[list[SentMessage]]:
    """Capture `ctzd.notify` messages instead of sending them."""
    messages: list[SentMessage] = []
    previous = _overrides.install(replace(_overrides.current(), outbox=messages))
    try:
        yield messages
    finally:
        _overrides.install(previous)
