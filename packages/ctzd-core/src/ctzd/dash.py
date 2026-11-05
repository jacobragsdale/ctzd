"""Dash helpers. Import as `from ctzd.dash import create_app`; needs the `ctzd-core[dash]` extra."""

import dash


def _healthz() -> tuple[str, int]:
    return ("ok", 200)


def create_app(name: str, *, title: str) -> dash.Dash:
    """A Dash app with the `/healthz` route the platform's readiness probe calls."""
    app = dash.Dash(name, title=title)
    app.server.add_url_rule("/healthz", endpoint="healthz", view_func=_healthz)
    return app
