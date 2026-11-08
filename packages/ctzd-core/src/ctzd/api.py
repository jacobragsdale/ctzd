"""A React app's server: its datasets under `/api` and its built pages at `/`, both served by basis-api.

`ctzd dev` and the app's container run it with uvicorn, as
`uvicorn --factory ctzd.api:asgi`, from the app's folder. The pages call
`/api/datasets/NAME` through the client generated from `openapi.json`, which
`write_openapi` writes, and which has no `/api` in its paths, as basis's
contract has it: the page's client adds the prefix. Health is at
`/health/live` and `/health/ready`.

Needs the `api` extra (`ctzd-core[api]`), which brings basis-api.
"""

import importlib
import inspect
import json
import operator
from pathlib import Path
from types import FunctionType
from typing import Any

from basis import api
from starlette.types import ASGIApp

from ctzd.datasets import Dataset, registered

DATASETS_MODULE = "app.api.datasets"
PAGES = "web/dist"
"""The built pages, relative to the app's folder, as basis-api serves a UI."""
SERVICE_NAME = "app"


def _handler(declared: Dataset) -> FunctionType:
    """The route's handler: the dataset's function, its signature saying it answers `api.Table[Row]`, which is what
    basis-api checks a handler's return against. The frame goes to basis-api as it is, which checks it once."""
    function = declared.function
    table = operator.getitem(api.Table, declared.row)

    def handler(*args: object, **kwargs: object) -> object:
        return function(*args, **kwargs)

    handler.__name__ = declared.name
    handler.__qualname__ = declared.name
    handler.__doc__ = function.__doc__
    handler.__module__ = function.__module__
    handler.__annotations__ = {**function.__annotations__, "return": table}
    handler.__dict__["__signature__"] = inspect.signature(function).replace(return_annotation=table)
    return handler


def service(module: str = DATASETS_MODULE, *, pages: str | None = None) -> api.Service:
    """The basis-api service of the datasets that `module` declares, serving the built pages in `pages` at `/`."""
    importlib.import_module(module)
    routes = [
        api.get(declared.path, _handler(declared), deadline_s=declared.deadline_s, response=operator.getitem(api.Table, declared.row), errors=[], dataset=True)
        for declared in registered(module).values()
    ]
    return api.Service(name=SERVICE_NAME, settings=api.Settings, routes=routes, ui=pages)


def openapi_document(module: str = DATASETS_MODULE) -> dict[str, Any]:
    """The API's OpenAPI document, which the page's client is generated from."""
    document: dict[str, Any] = api.build(service(module)).asgi.openapi()
    return document


def write_openapi(path: Path, module: str = DATASETS_MODULE) -> bool:
    """Write the document to `path` as stable JSON; return whether it changed."""
    text = json.dumps(openapi_document(module), indent=2, sort_keys=True) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def asgi(module: str = DATASETS_MODULE, *, pages: str = PAGES) -> ASGIApp:
    """The app: the datasets under `/api`, and the pages in `pages` at `/`, which answer 503 until they are built."""
    return api.build(service(module, pages=pages)).asgi
