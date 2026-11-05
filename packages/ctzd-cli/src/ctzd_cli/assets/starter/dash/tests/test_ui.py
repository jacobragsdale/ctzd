from dash import html

from app.ui.main import layout, server


def test_layout_builds_with_a_title() -> None:
    page = layout()
    assert isinstance(page.children, list)
    first = page.children[0]
    assert isinstance(first, html.H1)
    assert first.children == "{{title}}"


def test_health_endpoint_answers() -> None:
    response = server.test_client().get("/healthz")
    assert response.status_code == 200
