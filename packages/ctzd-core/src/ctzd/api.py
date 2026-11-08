"""A React app's server: its datasets, served by basis-api under `/api`, and its built pages at `/`.

`ctzd dev` and the app's container run it with uvicorn, as
`uvicorn --factory ctzd.api:asgi`, from the app's folder. The pages call
`/api/datasets/NAME` through the client generated from `openapi.json`, which
`write_openapi` writes, and which has no `/api` in its paths, as basis's
contract has it: the page's client adds the prefix.

Needs the `api` extra (`ctzd-core[api]`), which brings basis-api.
"""

import importlib
import inspect
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import FunctionType, GenericAlias
from typing import Any

from basis import api
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp

from ctzd.datasets import Dataset, registered, rows

DATASETS_MODULE = "app.api.datasets"
PAGES = Path("web/dist")
SERVICE_NAME = "app"


def _handler(declared: Dataset) -> FunctionType:
    """The route's handler: the dataset's parameters, as basis-api reads them, answering the frame's rows."""
    function = declared.function
    row_list = GenericAlias(list, (declared.row,))

    def handler(*args: object, **kwargs: object) -> list[dict[str, object]]:
        return rows(function(*args, **kwargs), declared.row, what=f"dataset {declared.name}")

    handler.__name__ = declared.name
    handler.__qualname__ = declared.name
    handler.__doc__ = function.__doc__
    handler.__module__ = function.__module__
    handler.__annotations__ = {**function.__annotations__, "return": row_list}
    handler.__dict__["__signature__"] = inspect.signature(function).replace(return_annotation=row_list)
    return handler


def service(module: str = DATASETS_MODULE) -> api.Service:
    """The basis-api service of the datasets that `module` declares."""
    importlib.import_module(module)
    routes = [
        api.get(declared.path, _handler(declared), deadline_s=declared.deadline_s, response=GenericAlias(list, (declared.row,)), errors=[], dataset=True) for declared in registered(module).values()
    ]
    return api.Service(name=SERVICE_NAME, settings=api.Settings, routes=routes)


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


def asgi(module: str = DATASETS_MODULE, *, pages: Path = PAGES) -> ASGIApp:
    """The app: basis-api's app for the datasets under `/api`, and the built pages at `/`.

    SHIM (MVP): basis step 2.14 serves a built UI at `/` from basis-api's own app;
    until it lands, ctzd mounts both and runs basis-api's lifespan itself.
    """
    built = api.build(service(module))
    inner = built.asgi

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncGenerator[None]:
        async with inner.router.lifespan_context(inner):
            yield

    return Starlette(routes=[Mount("/api", app=inner), Mount("/", app=StaticFiles(directory=pages, html=True, check_dir=False))], lifespan=lifespan)
