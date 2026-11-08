"""`ctzd add dataset`, `ctzd add page`, and `ctzd api list`: the citizen agent's loop, one command per slice.

`add dataset` writes a dataset with its row type, the loader it reads (and its
SQL file when it reads a source), and a test, then regenerates the page's client.
`add page` writes a page that shows a dataset as a chart and a grid, with each
column's format from the dataset's row type, adds its route, and links it from the
header. `api list` names each dataset's route, client function, parameters and
columns, so an agent never reads the generated client. Each one's files pass
`ctzd check` as written; the agent then edits the SQL, the row type and the page.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from tomlkit.items import Table

from ctzd_cli import appconfig, checks
from ctzd_cli.errors import CliError, UsageError

DATASETS = Path("src/app/api/datasets.py")
LOADERS = Path("src/app/data/loaders.py")
SQL = Path("src/app/data/sql")
TESTS = Path("tests")
PAGES = Path("web/src/pages")
ROUTES = Path("web/src/routes.ts")
NAV = Path("web/src/nav.ts")
OPENAPI = Path("web/openapi.json")
DATASET_NAME = re.compile(r"[a-z][a-z0-9_]{0,40}")
PAGE_NAME = re.compile(r"[a-z][a-z0-9-]{0,40}")
_FORMAT_KEY = "x-basis-format"
_EXAMPLE_QUERY = "-- Replace this with the real query. Bind values as :name on Oracle and %(name)s on SQL Server.\nSELECT 'example' AS label, 1.0 AS value\n"
_STEPS = {"32nd": "thirty_seconds", "half": "halves", "quarter": "quarters", "eighth": "eighths"}


def camel(name: str) -> str:
    """The client's name for an operation id: `sector_prices` is `sectorPrices`."""
    parts = [str(part) for part in re.split(r"[_-]", name)]
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def pascal(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[_-]", name))


def title(name: str) -> str:
    return " ".join(re.split(r"[_-]", name)).capitalize()


def _insert_import(text: str, line: str) -> str:
    """`text` with `line` after its last top-level import; the formatter sorts it on the next check."""
    lines = text.splitlines()
    last = max((index for index, found in enumerate(lines) if found.startswith(("import ", "from "))), default=-1)
    return "\n".join([*lines[: last + 1], line, *lines[last + 1 :]]) + "\n"


def _add_source(root: Path, source: str) -> bool:
    """List `source` in app.toml's `[sources].use`; return whether it was new."""
    path = root / appconfig.APP_FILE
    text = path.read_text(encoding="utf-8")
    if source in appconfig.parse(text).sources.use:
        return False
    document = tomlkit.parse(text)
    sources = document.get("sources")
    if not isinstance(sources, Table):
        sources = tomlkit.table()
        document["sources"] = sources
    current = sources.get("use")
    sources["use"] = [*(current if isinstance(current, list) else []), source]
    updated = tomlkit.dumps(document)
    appconfig.parse(updated)  # refuses a name app.toml cannot hold
    path.write_text(updated, encoding="utf-8")
    return True


def _write(root: Path, path: Path, text: str) -> str:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return path.as_posix()


def _dataset_test(name: str, source: str | None) -> str:
    route = name.replace("_", "-")
    if source is None:
        return f"""from starlette.testclient import TestClient

import ctzd.api


def test_{name}_answers_its_rows() -> None:
    with TestClient(ctzd.api.asgi("app.api.datasets")) as client:
        answer = client.get("/api/datasets/{route}")
    assert answer.status_code == 200
    assert [row["label"] for row in answer.json()] == ["North", "South"]
"""
    return f"""import pandas as pd
from starlette.testclient import TestClient

import ctzd.api


def test_{name}_answers_its_rows() -> None:
    rows = pd.DataFrame({{"label": ["North"], "value": [42.0]}})
    with ctzd.testing.fake_sources({source}={{"sql/{name}.sql": rows}}), TestClient(ctzd.api.asgi("app.api.datasets")) as client:
        answer = client.get("/api/datasets/{route}")
    assert answer.status_code == 200
    assert answer.json() == [{{"label": "North", "value": 42.0}}]
"""


def _loader(name: str, source: str | None) -> str:
    if source is None:
        return f'''

def load_{name}() -> pd.DataFrame:
    """The rows of the dataset `{name}`. Replace them with a query (see the ctzd-data skill)."""
    return pd.DataFrame({{"label": ["North", "South"], "value": [42.0, 17.0]}})
'''
    return f'''

@ctzd.cached(ttl="15m")
def load_{name}() -> pd.DataFrame:
    """The rows of the dataset `{name}`, from `{source}`."""
    frame = ctzd.query("{source}", "sql/{name}.sql")
    return ctzd.require_columns(frame, {{"label": "str", "value": "float"}}, what="{name}")
'''


def add_dataset(root: Path, name: str, source: str | None) -> list[str]:
    """Write the dataset `name` with its row type, loader, SQL file when it reads `source`, and test."""
    if DATASET_NAME.fullmatch(name) is None:
        msg = f"{name!r} is not a dataset name."
        raise UsageError(msg, hint="Use lowercase letters, digits, and underscores, starting with a letter, such as sector_prices.")
    datasets = (root / DATASETS).read_text(encoding="utf-8")
    loaders = (root / LOADERS).read_text(encoding="utf-8")
    if re.search(rf"^def {name}\(", datasets, re.MULTILINE) or re.search(rf"^def load_{name}\(", loaders, re.MULTILINE):
        msg = f"The app already has a dataset or loader named {name!r}."
        raise UsageError(msg, hint="Choose another name, or change the existing one.")
    files: list[str] = []
    if source is not None:
        if _add_source(root, source):
            files.append(appconfig.APP_FILE)
        files.append(_write(root, SQL / f"{name}.sql", "\n".join((f"-- The rows of the dataset `{name}`, one column per field of its row type in src/app/api/datasets.py.", _EXAMPLE_QUERY))))
    files.append(_write(root, LOADERS, loaders.rstrip("\n") + "\n" + _loader(name, source)))
    row = f"{pascal(name)}Row"
    addition = f'''

class {row}(ctzd.Row):
    """One row of `{name}`: one field per column the page shows."""

    label: str
    value: ctzd.Number


@ctzd.dataset({row})
def {name}() -> pd.DataFrame:
    """{title(name)}."""
    return load_{name}()
'''
    files.append(_write(root, DATASETS, _insert_import(datasets, f"from app.data.loaders import load_{name}").rstrip("\n") + "\n" + addition))
    files.append(_write(root, TESTS / f"test_dataset_{name}.py", _dataset_test(name, source)))
    return files


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    format: dict[str, object] | None


@dataclass(frozen=True)
class Dataset:
    name: str
    function: str
    path: str
    parameters: list[dict[str, object]]
    columns: list[Column]


def _document(root: Path) -> dict[str, object]:
    path = root / OPENAPI
    if not path.is_file():
        msg = "web/openapi.json is missing."
        raise CliError(msg, hint="Run `ctzd check --fast`, which writes it from the datasets.")
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        msg = "web/openapi.json is not an OpenAPI document."
        raise CliError(msg, hint="Run `ctzd check --fast`, which rewrites it.")
    return {str(key): value for key, value in loaded.items()}


def _object(value: object) -> dict[str, object]:
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}


def _kind(schema: dict[str, object]) -> str:
    options = schema.get("anyOf")
    kinds = [_object(option).get("type") for option in options] if isinstance(options, list) else [schema.get("type")]
    found = next((kind for kind in kinds if kind not in {None, "null"}), "any")
    return {"string": "text", "number": "number", "integer": "number", "boolean": "boolean"}.get(str(found), str(found))


def datasets(root: Path) -> list[Dataset]:
    """Every dataset the app's `web/openapi.json` declares."""
    document = _document(root)
    schemas = _object(_object(document.get("components")).get("schemas"))
    found: list[Dataset] = []
    for path, item in _object(document.get("paths")).items():
        operation = _object(_object(item).get("get"))
        if not path.startswith("/datasets/") or "operationId" not in operation:
            continue
        response = _object(_object(_object(_object(operation.get("responses")).get("200")).get("content")).get("application/json"))
        reference = str(_object(_object(response.get("schema")).get("items")).get("$ref", ""))
        row = _object(schemas.get(reference.rsplit("/", 1)[-1]))
        columns = [Column(name=name, kind=_kind(_object(schema)), format=_object(_object(schema).get(_FORMAT_KEY)) or None) for name, schema in _object(row.get("properties")).items()]
        declared = operation.get("parameters")
        parameters = [
            {"name": _object(parameter).get("name"), "required": bool(_object(parameter).get("required")), "type": _kind(_object(_object(parameter).get("schema")))}
            for parameter in (declared if isinstance(declared, list) else [])
        ]
        name = str(operation["operationId"])
        found.append(Dataset(name=name, function=camel(name), path="/api" + path, parameters=parameters, columns=columns))
    return found


def parallax_format(display: dict[str, object]) -> dict[str, object]:
    """A column's display format as parallax's grid and charts take it."""
    kind = display.get("kind")
    decimals = display.get("decimals")
    shown: dict[str, object] = {} if decimals is None else {"decimals": decimals}
    if kind == "pct":
        shown["percent"] = True
    elif kind == "bps":
        shown["bps"] = True
    elif kind == "price32":
        shown = {"thirty_seconds": _STEPS.get(str(display.get("step", "32nd")), "thirty_seconds")}
    return shown


def _page(name: str, dataset: Dataset) -> str:
    """SHIM (MVP): the page names each column's format until basis-ui reads them from the dataset (step 1.13)."""
    formats = {column.name: parallax_format(column.format) for column in dataset.columns if column.format is not None}
    numbers = [column.name for column in dataset.columns if column.kind == "number"]
    labels = [column.name for column in dataset.columns if column.kind == "text"]
    if not numbers:
        msg = f"The dataset {dataset.name} has no number column to chart."
        raise UsageError(msg, hint="Give its row type a number column, such as value: ctzd.Number.")
    x = labels[0] if labels else dataset.columns[0].name
    component = f"{pascal(name)}Page"
    return f"""/* {title(name)}: the rows of {dataset.name}, as a chart and a grid. */

import {{ Chart, Grid, useDataset }} from "@basis/parallax";
import {{ TypographyH1 }} from "@basis/ui";
import type {{ ReactElement }} from "react";

import {{ {dataset.function} }} from "#api/client.ts";

/** The dataset as the API answers it, an Arrow stream. */
const rows = async (): Promise<ArrayBuffer> => {{
  const answer = await {dataset.function}();
  return answer.data.arrayBuffer();
}};

const formats = {json.dumps(formats)};

function {component}(): ReactElement {{
  const dataset = useDataset("{dataset.name}", rows);
  return (
    <>
      <TypographyH1>{title(name)}</TypographyH1>
      <div className="h-80">
        <Chart
          dataset={{dataset}}
          layout={{{{ chart: {{ kind: "bar", x: "{x}", y: ["{numbers[0]}"] }}, formats }}}}
        />
      </div>
      <div className="h-80">
        <Grid dataset={{dataset}} layout={{{{ formats }}}} />
      </div>
    </>
  );
}}

export {{ {component} }};
"""


def add_route(root: Path, name: str) -> bool:
    """Add the page's route to routes.ts and its link to nav.ts; False when routes.ts no longer has the starter's shape."""
    routes_path = root / ROUTES
    routes = routes_path.read_text(encoding="utf-8")
    children = re.search(r"rootRoute\.addChildren\(\[([^\]]*)\]\)", routes)
    if children is None or "const routeTree" not in routes:
        return False
    variable = f"{camel(name)}Route"
    component = f"{pascal(name)}Page"
    routes = _insert_import(routes, f'import {{ {component} }} from "./pages/{name}.tsx";')
    route = f'const {variable} = createRoute({{\n  component: {component},\n  getParentRoute: () => rootRoute,\n  path: "/{name}",\n}});\n\n'
    routes = routes.replace("const routeTree", route + "const routeTree", 1)
    routes = re.sub(r"rootRoute\.addChildren\(\[([^\]]*)\]\)", lambda found: f"rootRoute.addChildren([{found.group(1).rstrip()}, {variable}])", routes, count=1)
    routes_path.write_text(routes, encoding="utf-8")
    nav_path = root / NAV
    nav = nav_path.read_text(encoding="utf-8") if nav_path.is_file() else ""
    end = re.search(r"\s*\] as const;", nav)
    if end is not None:
        before = nav[: end.start()].rstrip()
        comma = "" if before.endswith((",", "[")) else ","
        link = f'{comma}\n  {{ path: "/{name}", title: "{title(name)}" }},\n] as const;'
        nav_path.write_text(before + link + nav[end.end() :], encoding="utf-8")
    return True


def add_page(root: Path, name: str, dataset: str) -> tuple[list[str], bool]:
    """Write the page `name` showing `dataset`, route it, and link it; also return whether the route was added."""
    if PAGE_NAME.fullmatch(name) is None:
        msg = f"{name!r} is not a page name."
        raise UsageError(msg, hint="Use lowercase letters, digits, and hyphens, starting with a letter, such as sector-prices.")
    target = PAGES / f"{name}.tsx"
    if (root / target).exists():
        msg = f"web/src/pages/{name}.tsx already exists."
        raise UsageError(msg, hint="Choose another name, or change that page.")
    known = {found.name: found for found in datasets(root)}
    chosen = known.get(dataset)
    if chosen is None:
        msg = f"The app has no dataset named {dataset!r}."
        raise UsageError(msg, hint=f"Its datasets: {', '.join(sorted(known)) or 'none yet'}. `ctzd add dataset NAME` adds one.")
    required = [str(parameter["name"]) for parameter in chosen.parameters if parameter["required"]]
    if required:
        msg = f"The dataset {dataset} needs {', '.join(required)}, and a new page passes no parameters."
        raise UsageError(msg, hint="Give those parameters defaults in src/app/api/datasets.py, then add the page.")
    files = [_write(root, target, _page(name, chosen))]
    routed = add_route(root, name)
    if routed:
        files += [ROUTES.as_posix(), *([NAV.as_posix()] if (root / NAV).is_file() else [])]
    return files, routed


def refresh_client(root: Path) -> checks.Step:
    """Rewrite openapi.json and the client from the datasets, installing the page's packages first if needed."""
    if not (root / "web" / "node_modules").is_dir():
        installed = checks.web_install(root, "local")
        if installed.state == "failed":
            return installed
    return checks.client(root, "local")
