"""A React app's server: its datasets under `/api` and its built pages at `/`, both served by basis-api.

`ctzd dev` and the app's container run it with uvicorn, as
`uvicorn --factory ctzd.api:asgi`, from the app's folder. The pages call
`/api/datasets/NAME` through the client generated from `openapi.json`, which
`write_openapi` writes, and which has no `/api` in its paths, as basis's
contract has it: the page's client adds the prefix. Health is at
`/health/live` and `/health/ready`.

"""

import importlib
import json
import operator
import os
from pathlib import Path
from typing import Any

from basis import api
from starlette.types import ASGIApp

from ctzd import testing
from ctzd.datasets import registered

DATASETS_MODULE = "app.api.datasets"
FAKE_FIXTURES = "CTZD_FAKE_FIXTURES"
PAGES = "web/dist"
"""The built pages, relative to the app's folder, as basis-api serves a UI."""
SERVICE_NAME = "app"


def service(module: str = DATASETS_MODULE, *, pages: str | None = None) -> api.Service:
    """The basis-api service of the datasets that `module` declares, serving the built pages in `pages` at `/`."""
    importlib.import_module(module)
    routes = [
        api.get(declared.path, declared.function, deadline_s=declared.deadline_s, response=operator.getitem(api.Table, declared.row), errors=[], dataset=True)
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
    """The app: the datasets under `/api`, and the pages in `pages` at `/`, which answer 503 until they are built.

    With `CTZD_FAKE_FIXTURES` naming a folder, as `ctzd dev --fake` sets it, every source answers the fake rows
    in that folder instead (`ctzd.testing.fixture_sources`).
    """
    fixtures = os.environ.get(FAKE_FIXTURES)
    if fixtures is not None and fixtures != "":
        testing.serve_fixtures(Path(fixtures))
    return api.build(service(module, pages=pages)).asgi
