"""Fakes for app tests: sources, the table store, and the outbox.

    with ctzd.testing.fake_sources(fi_pricing={"sql/prices.sql": prices_frame()}):
        ...

A SQL fake maps a SQL file to a DataFrame, or to `by_params(function)` to
compute the frame from the bind parameters. A REST fake maps `"GET /path"` to
a JSON payload, or to `by_request(function)` to compute it from the
`httpx.Request`. Inside `fake_sources`, any source that is not faked fails
loudly, so a test can never reach a real system.
"""

import ast
import json
import re
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import get_args

import httpx
import pandas as pd

from ctzd import _overrides
from ctzd._arrow import Storage
from ctzd._errors import SourceError
from ctzd._messages import SentMessage
from ctzd.cache import clear_all
from ctzd.sources import check_name
from ctzd.validate import Family

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


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBER = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")
_LEADING_ZERO = re.compile(r"[-+]?0\d")
_BOOLEANS = frozenset({"True", "False", "true", "false"})
REST_FIXTURE = "rest.json"
"""`fixtures/SOURCE/rest.json`: a REST source's fake answers, keyed `"GET /path"` as `fake_sources` takes them."""
DATA_CODE = Path("src/app/data")
"""Where an app's loaders live, relative to the folder that holds `fixtures/`."""

type Columns = Mapping[str, Family]


def _is_id(column: str) -> bool:
    return column == "id" or column.endswith("_id")


def _typed(column: str, text: pd.Series, family: Family | None) -> pd.Series:
    """One fixture column from its text, as the query would bring it: by the loader's declared family, else by its look."""
    present = text.dropna()
    family = None if family == "any" else family
    if family == "str" or (family is None and _is_id(column)):
        return text
    if family == "datetime" or (family is None and not present.empty and present.str.fullmatch(_ISO_DATE).all()):
        return pd.to_datetime(text)
    if family == "bool" or (family is None and not present.empty and present.isin(_BOOLEANS).all()):
        return text.map(lambda value: None if pd.isna(value) else value.lower() == "true").astype("boolean")
    numeric = not present.empty and present.str.fullmatch(_NUMBER).all()
    # A number never starts with a zero, but an account (00123456) or a SEDOL (0263494) does: those stay text.
    if family == "float" or (family is None and numeric and not present.str.match(_LEADING_ZERO).any()):
        return pd.to_numeric(text).astype("float64")
    return text


def _fixture(path: Path, columns: Columns | None = None) -> pd.DataFrame:
    """A fixture's rows as the query brings them. Each column takes the family the loader's `require_columns` gives it;
    without one, a column named `id` or `..._id`, or a number written with a leading zero, stays text, as ids are
    selected; text that is every row an ISO date becomes a date; and other numbers are float64."""
    declared = columns or {}
    raw = pd.read_csv(path, dtype="str", keep_default_na=False, na_values=[""])
    return pd.DataFrame({column: _typed(str(column), raw[column], declared.get(str(column))) for column in raw.columns}, index=raw.index)


def _literal_columns(node: ast.expr) -> dict[str, Family] | None:
    if not isinstance(node, ast.Dict):
        return None
    found: dict[str, Family] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(value, ast.Constant)):
            return None
        family = next((name for name in get_args(Family) if name == value.value), None)
        if family is not None:
            found[key.value] = family
    return found


def _called(node: ast.Call, name: str) -> bool:
    return (isinstance(node.func, ast.Attribute) and node.func.attr == name) or (isinstance(node.func, ast.Name) and node.func.id == name)


def loader_columns(folder: Path) -> dict[tuple[str, str], Columns]:
    """For each `(source, "sql/NAME.sql")` a loader in `folder` queries, the column families its `require_columns` declares.

    Read from the code, not run: a loader that queries with string literals and checks with a literal dict is
    understood; any other is skipped, and its fixture is typed by the look of its values.
    """
    found: dict[tuple[str, str], dict[str, Family]] = {}
    for path in sorted(folder.rglob("*.py")) if folder.is_dir() else []:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError, UnicodeDecodeError:
            continue
        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)):
            calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
            queries = [
                (first.value, second.value)
                for call in calls
                if _called(call, "query") and len(call.args) >= 2  # noqa: PLR2004 - the source and the SQL file
                for first, second in [(call.args[0], call.args[1])]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and isinstance(second, ast.Constant) and isinstance(second.value, str)
            ]
            checks = [columns for call in calls if _called(call, "require_columns") and len(call.args) >= 2 and (columns := _literal_columns(call.args[1])) is not None]  # noqa: PLR2004
            for query in queries:
                for columns in checks:
                    found.setdefault(query, {}).update(columns)
    return {key: dict(value) for key, value in found.items()}


def fixture_sources(folder: Path) -> dict[str, dict[str, object]]:
    """The fake sources in `folder`, for `fake_sources(**fixture_sources(Path("fixtures")))` in a test and for `ctzd dev --fake`.

    `SOURCE/NAME.csv` is the rows `sql/NAME.sql` answers on a database source, typed by the loader's
    `require_columns` (see `loader_columns`); `SOURCE/rest.json` is a REST source's answers, an object keyed
    `"GET /path"` whose values are the JSON each request returns.
    """
    if not folder.is_dir():
        return {}
    columns = loader_columns(folder.parent / DATA_CODE)
    found: dict[str, dict[str, object]] = {}
    for source in sorted(entry for entry in folder.iterdir() if entry.is_dir()):
        rest = source / REST_FIXTURE
        csvs = sorted(source.glob("*.csv"))
        if rest.is_file():
            if csvs:
                msg = f"fixtures/{source.name} has both {REST_FIXTURE} and CSV files; a source is either a database or a REST API."
                raise SourceError(msg)
            found[source.name] = _rest_fixture(rest)
            continue
        routes: dict[str, object] = {f"sql/{csv.stem}.sql": _fixture(csv, columns.get((source.name, f"sql/{csv.stem}.sql"))) for csv in csvs}
        if routes:
            found[source.name] = routes
    return found


def _rest_fixture(path: Path) -> dict[str, object]:
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        msg = f"{path.parent.name}/{REST_FIXTURE} is not valid JSON: {err}"
        raise SourceError(msg) from None
    if not isinstance(loaded, dict) or not loaded or not all(isinstance(key, str) and _is_rest_route(key) for key in loaded):
        msg = f'{path.parent.name}/{REST_FIXTURE} must be an object keyed by request, such as {{"GET /bonds": [{{"isin": "US0001", "price": 99.5}}]}}.'
        raise SourceError(msg)
    return {str(key): value for key, value in loaded.items()}


def serve_fixtures(folder: Path) -> None:
    """Fake every source with the rows in `folder` for the rest of this process, as `ctzd dev --fake` serves the app:
    a source, SQL file, or REST route without fake rows fails, so the app never reaches a real system."""
    sql: dict[str, Mapping[str, _overrides.SqlFake]] = {}
    rest: dict[str, Mapping[str, object]] = {}
    for name, routes in fixture_sources(folder).items():
        if all(_is_rest_route(key) for key in routes):
            rest[name] = routes
        else:
            sql[name] = {key: _sql_fake(name, key, value) for key, value in routes.items()}
    _overrides.install(replace(_overrides.current(), sql=sql, rest=rest))
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
