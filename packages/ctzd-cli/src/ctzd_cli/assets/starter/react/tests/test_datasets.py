import ctzd.api
import pytest
from starlette.testclient import TestClient


def test_the_page_s_dataset_answers_every_category() -> None:
    with TestClient(ctzd.api.asgi("app.api.datasets")) as client:
        answer = client.get("/api/datasets/category-shares")
    assert answer.status_code == 200
    rows = answer.json()
    assert [row["category"] for row in rows] == ["North", "South", "East", "West"]
    assert sum(row["share"] for row in rows) == pytest.approx(1.0)
