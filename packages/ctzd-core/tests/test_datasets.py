"""A React app's datasets, served by basis-api under /api with the built pages at /."""

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pytest
from starlette.testclient import TestClient

from ctzd import api
from ctzd.datasets import FORMAT_KEY

ARROW = "application/vnd.apache.arrow.stream"
MODULE = '''
import pandas as pd

import ctzd


class Share(ctzd.Row):
    category: str
    value: ctzd.Number
    share: ctzd.Pct


@ctzd.dataset(Share)
def category_shares(minimum: float = 0.0) -> pd.DataFrame:
    """Each category's value and share."""
    frame = pd.DataFrame({"category": ["North", "South", "East"], "value": [3, 1, None], "share": [0.75, 0.25, None]})
    return frame[frame["value"].fillna(0) >= minimum]


@ctzd.dataset(Share)
def broken() -> pd.DataFrame:
    return pd.DataFrame({"category": ["North"]})
'''


@pytest.fixture
def module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"datasets_{tmp_path.name.replace('-', '_')}"
    (tmp_path / f"{name}.py").write_text(MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield name
    sys.modules.pop(name, None)


@pytest.fixture
def client(module: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.chdir(tmp_path)
    pages = tmp_path / "web" / "dist"
    pages.mkdir(parents=True)
    (pages / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    with TestClient(api.asgi(module), raise_server_exceptions=False) as test_client:
        yield test_client


def test_a_dataset_answers_its_rows_as_json_with_floats_and_nulls(client: TestClient) -> None:
    answer = client.get("/api/datasets/category-shares")
    assert answer.status_code == 200
    assert answer.json() == [{"category": "North", "value": 3.0, "share": 0.75}, {"category": "South", "value": 1.0, "share": 0.25}, {"category": "East", "value": None, "share": None}]


def test_a_dataset_answers_arrow_with_float64_columns(client: TestClient) -> None:
    answer = client.get("/api/datasets/category-shares", headers={"Accept": ARROW})
    assert answer.status_code == 200
    table = pa.ipc.open_stream(answer.content).read_all()
    assert table.schema.field("value").type == pa.float64()
    assert table.column("value").to_pylist() == [3.0, 1.0, None]


def test_the_function_s_arguments_are_query_parameters(client: TestClient) -> None:
    answer = client.get("/api/datasets/category-shares", params={"minimum": "2"})
    assert [row["category"] for row in answer.json()] == ["North"]


def test_a_frame_without_a_declared_column_fails_the_request(client: TestClient) -> None:
    assert client.get("/api/datasets/broken").status_code == 500


def test_the_built_pages_are_served_at_the_root(client: TestClient) -> None:
    answer = client.get("/")
    assert answer.status_code == 200
    assert "id=root" in answer.text


def test_the_app_is_ready_and_a_page_route_answers_the_pages(client: TestClient) -> None:
    assert client.get("/health/ready").status_code == 200
    assert "id=root" in client.get("/categories/north").text


def test_a_path_under_api_without_a_route_is_not_found(client: TestClient) -> None:
    assert client.get("/api/nothing").status_code == 404


def test_pages_that_are_not_built_answer_503_and_the_datasets_still_answer(module: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with TestClient(api.asgi(module), raise_server_exceptions=False) as test_client:
        assert test_client.get("/").status_code == 503
        assert test_client.get("/api/datasets/category-shares").status_code == 200


def test_the_document_names_each_dataset_and_each_column_s_format(module: str) -> None:
    document = api.openapi_document(module)
    assert document["paths"]["/datasets/category-shares"]["get"]["operationId"] == "category_shares"
    share = document["components"]["schemas"]["Share"]["properties"]
    assert share["value"][FORMAT_KEY] == {"kind": "number", "decimals": 2}
    assert share["share"][FORMAT_KEY] == {"kind": "pct"}


def test_write_openapi_writes_stable_json_once(module: str, tmp_path: Path) -> None:
    target = tmp_path / "web" / "openapi.json"
    assert api.write_openapi(target, module) is True
    assert api.write_openapi(target, module) is False
    assert json.loads(target.read_text(encoding="utf-8"))["paths"]
