"""A dash app driven as a browser drives it, in the process, with no browser and no socket: `ctzd check`'s page checks
and `ctzd render`. The CLI ships this file and runs it inside the app's own environment, on the fakes:

    python drive.py check                              every page and callback, as `ctzd check` checks them
    python drive.py render PAGE [ID.PROP=VALUE ...]    what a page shows after its load

It prints one JSON object. Forked from basis-dash's `testing.py`, `driving.py`, `checks.py` and `render.py`, without
their declarative layer: the app is plain Dash (`app.app:app`, its pages under `app.pages`).

**The checks.** Every page builds, inside the shell; every input, output and state of every callback names a
component the page has (the shell's, or that page's, for a page's own callbacks) and a property that component's type
has; every callback that reads data is `async` and reads through `ui.load`; and every callback a page load calls is
called in the browser's order, each with the values the layout and the callbacks before it give, and must neither
fail, nor hold the server's event loop longer than `LOOP_BUDGET_MS`, nor answer more than `BUDGET_BYTES`, a figure
Plotly refuses, or rows that leave a grid's column empty. A callback with a pattern-matching id, a clientside one, and
one with `prevent_initial_call` is checked by its ids alone.
"""

import ast
import base64
import importlib
import inspect
import json
import logging
import sys
import textwrap
import time
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Final, override

import anyio
import orjson
from fastapi.testclient import TestClient

type Json = Any
"""A value as a layout or an answer holds it, tested with `isinstance` before it is relied on."""
type Node = dict[str, Json]
type Problem = tuple[str, str]

ROUTER: Final = "_pages_content.children"
UPDATE: Final = "_dash-update-component"
DEPENDENCIES: Final = "_dash-dependencies"
LAYOUT: Final = "_dash-layout"
LOOP_BUDGET_MS: Final = 250.0
BUDGET_BYTES: Final = 4 * 1_048_576
TICK_S: Final = 0.005
FIRST: Final = 10
UI_LOG: Final = "ctzd.ui"
NAMESPACES: Final[Mapping[str, str]] = {"dash_html_components": "dash.html", "dash_core_components": "dash.dcc", "dash_table": "dash.dash_table"}
_WILDCARDS: Final = frozenset({"ALL", "MATCH", "ALLSMALLER"})


# ---------------------------------------------------------------- the app, built and driven


@dataclass
class Watch:
    """The longest the event loop has been held, in seconds, since `worst` was last set to 0."""

    worst: float = 0.0


@dataclass(frozen=True)
class Call:
    output: str
    status: int
    content: bytes
    response: Mapping[str, Mapping[str, Json]]
    side: Mapping[str, Mapping[str, Json]]
    failures: tuple[str, ...]
    held_ms: float

    @property
    def failed(self) -> bool:
        return bool(self.failures) or self.status not in {HTTPStatus.OK, HTTPStatus.NO_CONTENT}


@dataclass
class Drive:
    app: Any
    http: TestClient
    records: list[logging.LogRecord]
    watch: Watch = field(default_factory=Watch)
    _dependencies: list[dict[str, Json]] | None = None

    def get(self, path: str) -> Json:
        answered = self.http.get(f"/{path}")
        if answered.status_code != HTTPStatus.OK:
            msg = f"the app answered {answered.status_code} at /{path}: {answered.text[:300]}"
            raise PageError(msg)
        return answered.json()

    @property
    def dependencies(self) -> list[dict[str, Json]]:
        if self._dependencies is None:
            found = self.get(DEPENDENCIES)
            self._dependencies = [entry for entry in found if isinstance(entry, dict)] if isinstance(found, list) else []
        return self._dependencies

    def functions(self) -> dict[str, Callable[..., object] | None]:
        """Each callback's output, with its function as its module wrote it, or None for a clientside one."""
        found: dict[str, Callable[..., object] | None] = {}
        for output, entry in self.app.callback_map.items():
            function = entry.get("callback")
            function = getattr(function, "__wrapped__", function)
            found[str(output)] = function if callable(function) else None
        return found

    def module(self, output: str) -> str | None:
        module = getattr(self.functions().get(output), "__module__", None)
        return module if isinstance(module, str) else None

    def pages(self) -> list[dict[str, Json]]:
        import dash  # noqa: PLC0415 - the app's own Dash

        return [dict(page) for page in dash.page_registry.values()]

    def page(self, path: str) -> list[Node]:
        """The components of the page at `path`, inside the shell, outermost first."""
        del self.records[:]
        router = next((entry for entry in self.dependencies if ROUTER in str(entry.get("output"))), None)
        if router is None:
            msg = "the app has no pages: build it with ui.dash_app, which turns Dash Pages on"
            raise PageError(msg)
        values = {"pathname": path, "search": "", "hash": "", "href": f"http://testserver{path}"}
        inputs = [{"id": one["id"], "property": one["property"], "value": values.get(str(one["property"]))} for one in router.get("inputs") or []]
        state = [{"id": one["id"], "property": one["property"], "value": None} for one in router.get("state") or []]
        answered = self.http.post(f"/{UPDATE}", json=request(str(router["output"]), inputs, state=state))
        if answered.status_code != HTTPStatus.OK:
            said = next((record.getMessage() for record in self.records if record.levelno >= logging.ERROR), f"the app answered {answered.status_code}")
            raise PageError(said)
        content = (answered.json().get("response") or {}).get("_pages_content", {}).get("children")
        return list(nodes(replaced(self.get(LAYOUT), content)))

    @staticmethod
    def first(path: str, components: Sequence[Node]) -> dict[str, Json]:
        """Each property of each component with an id, by `ID.PROP`, as the layout gives it, and the location."""
        values: dict[str, Json] = {}
        for node in components:
            props = node.get("props") or {}
            if isinstance(component := props.get("id"), str):
                values |= {f"{component}.{prop}": value for prop, value in props.items()}
        return values | {"_pages_location.pathname": path, "_pages_location.search": "", "_pages_location.hash": "", "_pages_location.href": f"http://testserver{path}"}

    def calls(self, page_module: str | None, components: Sequence[Node], *, skip: frozenset[int] = frozenset()) -> list[dict[str, Json]]:
        """The callbacks a load of the page calls, of the app's and the page's modules, in the browser's order."""
        on = {str(props["id"]) for node in components if isinstance((props := node.get("props") or {}).get("id"), str)}
        functions = self.functions()
        found = [
            entry
            for entry in self.dependencies
            if not str(entry.get("output", "")).startswith(".._pages_content")
            and self.module(str(entry["output"])) in {"app.app", page_module}
            and id(functions.get(str(entry["output"]))) not in skip
            and called_on_load(entry, on)
        ]
        return ordered(found)

    def call(self, entry: Mapping[str, Json], values: dict[str, Json], *, again_past_ms: float | None = None) -> Call:
        """Call the callback `entry` with `values`, keep what it answers in them, and give what it answered; made once
        more where it held the loop past `again_past_ms`, as Python's first import of a module does."""
        output = str(entry["output"])
        inputs = [{"id": one["id"], "property": one["property"], "value": values.get(f"{one['id']}.{one['property']}")} for one in entry.get("inputs") or []]
        state = [{"id": one["id"], "property": one["property"], "value": values.get(f"{one['id']}.{one['property']}")} for one in entry.get("state") or []]
        body = request(output, inputs, state=state)
        answered = None
        for attempt in range(2):
            del self.records[:]
            self.watch.worst = 0.0
            answered = self.http.post(f"/{UPDATE}", json=body)
            if (portal := self.http.portal) is not None:
                portal.call(anyio.sleep, 4 * TICK_S)
            if again_past_ms is None or self.watch.worst * 1000 <= again_past_ms or attempt:
                break
        assert answered is not None  # noqa: S101 - the loop runs at least once
        failures = tuple(record.getMessage() for record in self.records if record.name == UI_LOG and record.levelno >= logging.ERROR)
        decoded: Json = orjson.loads(answered.content) if answered.status_code == HTTPStatus.OK and answered.content else {}
        response = _props(decoded.get("response")) if isinstance(decoded, dict) else {}
        side = _props(decoded.get("sideUpdate")) if isinstance(decoded, dict) else {}
        for component, props in response.items():
            values |= {f"{component}.{prop}": value for prop, value in props.items()}
        return Call(output, answered.status_code, answered.content, response, side, failures, self.watch.worst * 1000)


class PageError(RuntimeError):
    """A page that does not build; the message says why, as the app logged it."""


def request(output: str, inputs: Sequence[Mapping[str, object]], *, state: Sequence[Mapping[str, object]] = ()) -> dict[str, object]:
    """The body of `POST _dash-update-component` that fires the callback whose output is `output`."""
    return {"output": output, "outputs": _outputs_body(output), "inputs": list(inputs), "state": list(state), "changedPropIds": []}


def _outputs_body(output: str) -> object:
    ones = [{"id": one["id"], "property": one["property"]} for one in outputs(output)]
    return ones if output.startswith("..") else ones[0]


@contextmanager
def driving() -> Generator[Drive]:
    """The app at `app.app:app`, built and started through FastAPI's test client, its log records kept, and its event
    loop watched."""
    with captured() as records:
        module = importlib.import_module("app.app")
        app = module.app
        app._setup_server()  # noqa: SLF001 - where Dash raises a callback's setup errors, which a check must see
        with TestClient(app.server) as http:
            drive = Drive(app, http, records)
            portal = http.portal
            watching = portal.start_task_soon(_watching, drive.watch) if portal is not None else None
            try:
                yield drive
            finally:
                if watching is not None:
                    watching.cancel()


async def _watching(watch: Watch) -> None:
    while True:
        before = time.perf_counter()
        await anyio.sleep(TICK_S)
        watch.worst = max(watch.worst, time.perf_counter() - before - TICK_S)


@contextmanager
def captured() -> Generator[list[logging.LogRecord]]:
    """Every log record meanwhile, kept rather than written."""
    records: list[logging.LogRecord] = []

    class Keep(logging.Handler):
        @override
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    kept: list[logging.Handler] = [Keep()]
    root.handlers, root.level = kept, logging.INFO
    try:
        yield records
    finally:
        root.handlers, root.level = handlers, level


def _props(found: Json) -> dict[str, dict[str, Json]]:
    if not isinstance(found, dict):
        return {}
    return {str(component): {str(prop): value for prop, value in props.items()} for component, props in found.items() if isinstance(props, dict)}


def nodes(tree: Json) -> Iterator[Node]:
    """Each component of a layout as Dash sends it, at any depth, outermost first."""
    if isinstance(tree, dict):
        if "type" in tree and "namespace" in tree and isinstance(tree.get("props"), dict):
            yield tree
            for value in tree["props"].values():
                yield from nodes(value)
        else:
            for value in tree.values():
                yield from nodes(value)
    elif isinstance(tree, list):
        for value in tree:
            yield from nodes(value)


def replaced(chrome: Json, content: Json) -> Json:
    """The shell, with the page's content where Dash Pages puts it."""
    if isinstance(chrome, dict):
        props = chrome.get("props")
        if isinstance(props, dict) and props.get("id") == "_pages_content":
            return {**chrome, "props": {**props, "children": content}}
        return {key: replaced(value, content) for key, value in chrome.items()}
    if isinstance(chrome, list):
        return [replaced(value, content) for value in chrome]
    return chrome


def outputs(output: str) -> list[dict[str, str]]:
    ones = output[2:-2].split("...") if output.startswith("..") and output.endswith("..") else [output]
    found: list[dict[str, str]] = []
    for one in ones:
        component, _, prop = one.rpartition(".")
        found.append({"id": component, "property": prop.partition("@")[0]})
    return found


def short(output: str) -> str:
    given = outputs(output)
    first = given[0] if given else {"id": output, "property": ""}
    more = f" (and {len(given) - 1} more)" if len(given) > 1 else ""
    return f"{first['id']}.{first['property']}{more}"


def called_on_load(entry: Mapping[str, Json], on: set[str]) -> bool:
    if entry.get("clientside_function") or entry.get("prevent_initial_call"):
        return False
    given = [*outputs(str(entry["output"])), *(entry.get("inputs") or []), *(entry.get("state") or [])]
    if any(str(one["id"]).startswith("{") for one in given):
        return False
    return all(str(one["id"]) in on or str(one["id"]).startswith("_pages_") for one in given)


def ordered(entries: list[dict[str, Json]]) -> list[dict[str, Json]]:
    """`entries` in the order the browser calls them: one whose input another answers after that one."""
    produced = {f"{one['id']}.{one['property']}": index for index, entry in enumerate(entries) for one in outputs(str(entry["output"]))}
    waits = {index: {produced[key] for one in entry.get("inputs") or [] if (key := f"{one['id']}.{one['property']}") in produced and produced[key] != index} for index, entry in enumerate(entries)}
    done: list[int] = []
    while len(done) < len(entries):
        ready = [index for index in range(len(entries)) if index not in done and waits[index] <= set(done)]
        done.extend(ready or [next(index for index in range(len(entries)) if index not in done)])
    return [entries[index] for index in done]


# ---------------------------------------------------------------- reads only through ui.load


def _data_names(module: object) -> tuple[set[str], set[str]]:
    """The names in `module` bound to a function of `app.data`, and those bound to `app.data` modules themselves."""
    functions: set[str] = set()
    modules: set[str] = set()
    for name, value in vars(module).items():
        if inspect.ismodule(value) and value.__name__.startswith("app.data"):
            modules.add(name)
        elif callable(value) and str(getattr(value, "__module__", "")).startswith("app.data"):
            functions.add(name)
    return functions, modules


def _ui_names(module: object) -> set[str]:
    return {name for name, value in vars(module).items() if inspect.ismodule(value) and value.__name__ == "app.ui"}


def read_problems(function: Callable[..., object]) -> list[Problem]:
    """How a callback breaks the rule that it reads data only by awaiting `ui.load` in an `async def`."""
    module = sys.modules.get(str(getattr(function, "__module__", "")))
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    except OSError, TypeError, SyntaxError:
        return []
    if module is None:
        return []
    data_functions, data_modules = _data_names(module)
    ui_modules = _ui_names(module)
    name = f"{module.__name__}.{getattr(function, '__qualname__', 'callback')}"
    found: list[Problem] = []
    loads = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        if isinstance(called, ast.Attribute) and isinstance(called.value, ast.Name) and called.value.id in ui_modules and called.attr == "load":
            loads = True
        direct = (isinstance(called, ast.Name) and called.id in data_functions) or (isinstance(called, ast.Attribute) and isinstance(called.value, ast.Name) and called.value.id in data_modules)
        if direct:
            what = ast.unparse(called)
            found.append((f"callback {name} calls {what}() itself, which runs the read on the server's one event loop", f"read it with `await ui.load({what}, ...)` in an async callback"))
    if (loads or found) and not inspect.iscoroutinefunction(function):
        found.append((f"callback {name} reads data and is not async, so it would hold the server for everyone while it reads", "make it `async def` and `await ui.load(...)`"))
    return found


# ---------------------------------------------------------------- the checks


def _properties(node: Mapping[str, Json]) -> tuple[frozenset[str], tuple[str, ...]] | None:
    namespace, kind = str(node.get("namespace")), str(node.get("type"))
    try:
        module = importlib.import_module(NAMESPACES.get(namespace, namespace))
    except ImportError:
        return None
    made = getattr(module, kind, None)
    if not isinstance(made, type):
        return None
    try:
        names = [name for name in inspect.signature(made.__init__).parameters if name not in {"self", "args", "kwargs"}]
    except TypeError, ValueError:
        return None
    return frozenset(names), ("data-", "aria-")


def _matches(given: Json, wanted: str | dict[str, Json]) -> bool:
    if isinstance(wanted, str):
        return isinstance(given, str) and given == wanted
    if not isinstance(given, dict) or set(given) != set(wanted):
        return False
    return all(value == given[key] or (isinstance(value, list) and value and value[0] in _WILDCARDS) for key, value in wanted.items())


def _parsed(component: str) -> str | dict[str, Json]:
    if component.startswith("{"):
        with suppress(orjson.JSONDecodeError):
            found: Json = orjson.loads(component)
            if isinstance(found, dict):
                return {str(key): value for key, value in found.items()}
    return component


def id_problems(entry: Mapping[str, Json], components: list[Node], where: str) -> list[Problem]:
    found: list[Problem] = []
    output = str(entry["output"])
    for role, given in [("output", outputs(output)), ("input", entry.get("inputs") or []), ("state", entry.get("state") or [])]:
        for one in given:
            component, prop = _parsed(str(one["id"])), str(one["property"])
            matched = [node for node in components if _matches((node.get("props") or {}).get("id"), component)]
            if not matched:
                if isinstance(component, str):
                    found.append((f"callback {short(output)}'s {role} {component}.{prop} names no component of {where}", "give a component that id in the page's layout, or name one it has"))
                continue
            for node in matched:
                if (known := _properties(node)) is not None and prop not in known[0] and not prop.startswith(known[1]):
                    listed = ", ".join(sorted(name for name in known[0] if name != "id")[:12])
                    found.append((f"callback {short(output)}'s {role} {component}.{prop} names a property {node.get('type')} does not have", f"name one of its properties, as {listed}"))
    return found


def _bad_figure(value: Json) -> str | None:
    import plotly.graph_objects as go  # noqa: PLC0415 - Plotly loads when a figure is checked

    if not isinstance(value, dict):
        return f"it is a {type(value).__name__}, not a figure"
    try:
        go.Figure(value)
    except ValueError as error:
        return " ".join(str(error).split())[:300]
    return None


def _empty_columns(node: Mapping[str, Json], value: Json) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(row, dict) for row in value):
        return []
    fields = [str(column["field"]) for column in (node.get("props") or {}).get("columnDefs") or [] if isinstance(column, dict) and "field" in column]
    held = {key for row in value for key in row}
    return [name for name in fields if name not in held]


def answer_problems(call: Call, path: str, kinds: Mapping[str, Node]) -> list[Problem]:
    name, start = short(call.output), f"from page {path}'s first values"
    if call.failed:
        said = call.failures[0] if call.failures else f"the app answered {call.status}: {call.content[:300].decode(errors='replace')}"
        return [(f"callback {name} failed on the sample rows, {start}: {said}", f"fix it, as `ctzd render {path}` shows it")]
    found: list[Problem] = []
    if call.held_ms > LOOP_BUDGET_MS:
        found.append(
            (
                f"callback {name} held the server's event loop for {call.held_ms:.0f} ms on the sample rows, past the {LOOP_BUDGET_MS:.0f} ms every user's page waits on",
                "move the work into data/ or logic/ and await it through ui.load, or narrow what it computes",
            )
        )
    if len(call.content) > BUDGET_BYTES:
        found.append((f"callback {name} answered {len(call.content) // 1024} KB on the sample rows, past the {BUDGET_BYTES // 1_048_576} MB budget", "send fewer rows or columns, or a summary"))
    for component, props in call.response.items():
        node = kinds.get(component)
        kind = f"{node.get('namespace')}.{node.get('type')}" if node is not None else None
        for prop, value in props.items():
            if kind == "dash_core_components.Graph" and prop == "figure" and (why := _bad_figure(value)) is not None:
                found.append((f"callback {name} answers {component}.figure with a figure Plotly refuses: {why}", "build it with ui.bar, ui.line or ui.scatter"))
            if kind == "dash_ag_grid.AgGrid" and prop == "rowData" and node is not None and (empty := _empty_columns(node, value)):
                found.append(
                    (f"callback {name} answers {component}.rowData with rows that hold no {', '.join(empty)}, a column of the grid", "answer ui.rows of a frame with every column the grid names")
                )
    return found


def check() -> dict[str, Json]:
    problems: list[Problem] = []
    try:
        context = driving()
        drive = context.__enter__()
    except Exception as error:  # noqa: BLE001 - the app's own code, whatever it raises
        return {"problems": [(f"the dashboard does not build on the sample rows: {type(error).__name__}: {' '.join(str(error).split())[:500]}", "fix it, as `ctzd dev --fake` shows it in its log")]}
    try:
        functions = drive.functions()
        skipped: set[int] = set()
        for function in functions.values():
            if function is not None and (found := read_problems(function)):
                problems += found
                skipped.add(id(function))
        pages: dict[str, tuple[str | None, list[Node]]] = {}
        for page in drive.pages():
            path, module = str(page.get("path")), page.get("module")
            try:
                pages[path] = (module if isinstance(module, str) else None, drive.page(path))
            except PageError as error:
                problems.append((f"page {path} does not build on the sample rows: {error}", f"fix its layout, as `ctzd render {path}` shows it"))
        for entry in drive.dependencies:
            output = str(entry["output"])
            if output.startswith(".._pages_content"):
                continue
            module = drive.module(output)
            on = [path for path, (page_module, _) in pages.items() if page_module == module] or list(pages)
            components = [node for path in on for node in pages[path][1]]
            problems += id_problems(entry, components, f"page {on[0]}" if len(on) == 1 else "any page")
        for path, (module, components) in pages.items():
            values = drive.first(path, components)
            kinds = {str(props["id"]): node for node in components if isinstance((props := node.get("props") or {}).get("id"), str)}
            for entry in drive.calls(module, components, skip=frozenset(skipped)):
                problems += answer_problems(drive.call(entry, values, again_past_ms=LOOP_BUDGET_MS), path, kinds)
        return {"problems": problems, "pages": sorted(pages)}
    finally:
        context.__exit__(None, None, None)


# ---------------------------------------------------------------- render


def _spec(column: Mapping[str, Json]) -> dict[str, Json] | None:
    """A grid column's format spec, as `ui.column` writes it into its valueFormatter."""
    formatter = column.get("valueFormatter")
    text = formatter.get("function") if isinstance(formatter, dict) else None
    if not isinstance(text, str) or not text.startswith("ctzdFormat(params.value, "):
        return None
    with suppress(ValueError):
        found = json.loads(text.removeprefix("ctzdFormat(params.value, ").removesuffix(")"))
        return found if isinstance(found, dict) else None
    return None


def shown_text(value: Json, spec: Mapping[str, Json] | None) -> Json:
    """A cell as the grid shows it: a number through `ctzd_common.display`, in the column's format."""
    if spec is None or not isinstance(value, int | float) or isinstance(value, bool):
        return value
    from ctzd_common import display  # noqa: PLC0415 - the app's own copy

    if "thirtySeconds" in spec:
        steps = {count: step for step, count in display.STEPS.items()}
        return display.text(float(value), display.Format("price32", step=steps.get(int(spec["thirtySeconds"]), "32nd")))
    decimals = int(spec.get("decimals", 2))
    if spec.get("percent"):
        return display.text(float(value), display.Format("pct", decimals))
    if spec.get("bps"):
        return display.text(float(value), display.Format("bps", decimals))
    if symbol := spec.get("symbol"):
        currency = next((code for code, (sign, _) in display.CURRENCIES.items() if sign == symbol), None)
        if currency is not None:
            return display.text(float(value), display.Format("money", decimals, currency=currency))
    return display.text(float(value), display.Format("number", decimals))


def _columns(node: Node) -> list[tuple[str, str, dict[str, Json] | None]]:
    defined = (node.get("props") or {}).get("columnDefs") or []
    return [(str(column["field"]), str(column.get("headerName") or column["field"]), _spec(column)) for column in defined if isinstance(column, dict) and "field" in column and not column.get("hide")]


def _strings(value: Json) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, int | float):
        return [str(value)]
    if isinstance(value, list):
        return [found for inner in value for found in _strings(inner)]
    if isinstance(value, dict) and isinstance(props := value.get("props"), dict):
        return _strings(props.get("children"))
    return []


def _length(given: Json) -> int | None:
    if isinstance(given, list):
        return len(given)
    if isinstance(given, dict) and isinstance(given.get("bdata"), str) and isinstance(given.get("dtype"), str):
        import numpy as np  # noqa: PLC0415 - a typed array's length needs its item size

        return len(base64.b64decode(given["bdata"])) // np.dtype(given["dtype"]).itemsize
    return None


def _title(given: Json) -> str | None:
    if isinstance(given, dict):
        given = given.get("text")
    return given if isinstance(given, str) else None


def summary(node: Node | None, prop: str, value: Json) -> dict[str, Json]:
    """What a person sees of `value`, given to `node`'s `prop`."""
    kind = f"{node.get('namespace')}.{node.get('type')}" if node is not None else None
    if node is not None and kind == "dash_ag_grid.AgGrid" and prop == "rowData" and isinstance(value, list):
        columns = _columns(node)
        first = [{header: shown_text(row.get(field), spec) for field, header, spec in columns} for row in value[:FIRST] if isinstance(row, dict)]
        return {"kind": "grid", "rows": len(value), "columns": [header for _, header, _ in columns], "first": first}
    if prop == "figure" and isinstance(value, dict):
        layout = value.get("layout") if isinstance(value.get("layout"), dict) else {}
        traces = [
            {"type": trace.get("type", "scatter"), "name": trace.get("name"), "points": next((found for axis in ("x", "y", "values") if (found := _length(trace.get(axis))) is not None), 0)}
            for trace in value.get("data") or []
            if isinstance(trace, dict)
        ]
        axes = {name: {"title": _title(axis.get("title")), "format": axis.get("tickformat")} for name, axis in sorted(layout.items()) if name.startswith(("xaxis", "yaxis")) and isinstance(axis, dict)}
        notes = [note.get("text") for note in layout.get("annotations") or [] if isinstance(note, dict)]
        return {"kind": "figure", "traces": traces, "axes": axes, **({"says": notes} if notes else {})}
    if prop in {"children", "style"} or isinstance(value, str | int | float):
        return {"kind": "text", "text": " ".join(_strings(value))} if prop != "style" else {"kind": "style", "value": value}
    return {"kind": "value", "value": value}


def render(page: str, sets: Mapping[str, Json]) -> dict[str, Json]:
    with driving() as drive:
        known = {str(found.get("path")): found for found in drive.pages()}
        chosen = known.get(page) or known.get(f"/{page.lstrip('/')}") or next((found for found in known.values() if str(found.get("name", "")).casefold() == page.casefold()), None)
        if chosen is None:
            return {"error": f"the app has no page {page!r}: name one by its path or name, of {', '.join(f'{path} ({found.get("name")})' for path, found in known.items())}"}
        path, module = str(chosen["path"]), chosen.get("module")
        components = drive.page(path)
        kinds = {str(props["id"]): node for node in components if isinstance((props := node.get("props") or {}).get("id"), str)}
        if unknown := sorted(key for key in sets if key.rpartition(".")[0] not in kinds):
            return {"error": f"page {path} has no component {unknown[0].rpartition('.')[0]!r}: --set ID.PROP=VALUE names one of {', '.join(sorted(kinds)[:20])}"}
        values = drive.first(path, components) | dict(sets)
        shown: dict[str, Json] = {}
        failed: list[dict[str, str]] = []
        notifications: list[Json] = []
        sample = False
        loads: list[str] = []
        for entry in drive.calls(module if isinstance(module, str) else None, components):
            called = drive.call(entry, values)
            loads += [record.getMessage() for record in drive.records if record.name == UI_LOG and record.getMessage().startswith("loaded ")]
            sent = called.side.get("ctzd-notifications", {}).get("sendNotifications")
            notifications += [{"title": note.get("title"), "message": note.get("message")} for note in sent if isinstance(note, dict)] if isinstance(sent, list) else []
            sample = sample or "ctzd-sample-banner" in called.side
            if called.failed:
                failed.append({"callback": short(called.output), "error": called.failures[0] if called.failures else f"the app answered {called.status}"})
                continue
            for component, props in called.response.items():
                for prop, value in props.items():
                    shown[f"{component}.{prop}"] = summary(kinds.get(component), prop, value)
        return {"page": path, "title": chosen.get("title"), "sample_data": sample, "outputs": shown, "loads": loads, "notifications": notifications, "failed": failed}


def main(argv: list[str]) -> int:
    match argv:
        case ["check"]:
            found = check()
        case ["render", page, *given]:
            sets: dict[str, Json] = {}
            for one in given:
                key, equals, value = one.partition("=")
                if not equals:
                    sys.stdout.write(json.dumps({"error": f"--set {one} names no value: give --set ID.PROP=VALUE"}) + "\n")
                    return 2
                try:
                    sets[key] = json.loads(value)
                except json.JSONDecodeError:
                    sets[key] = value
            found = render(page, sets)
        case _:
            sys.stderr.write(__doc__ or "")
            return 2
    sys.stdout.write(json.dumps(found, default=str) + "\n")
    return 1 if found.get("error") or found.get("problems") or found.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
