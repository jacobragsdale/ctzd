---
name: ctzd-dash
description: "Build and test Dash pages in a ctzd app: ctzd.dash.create_app, layout as a function, callbacks as plain typed functions tested directly, dash-ag-grid tables, cached loaders, and the starter test pattern. Use when the app's ui is dash."
---

# Dash in a ctzd app

The platform serves `app.ui.main:server` with gunicorn (2 worker processes,
4 threads each); `ctzd dev` runs `app.run(debug=True)` from the same module.

## Rules

- `src/app/ui/main.py` defines `app = create_app(__name__, title=...)` and
  `server = app.server`. Keep both names: the platform runs them, and
  `create_app` adds the `/healthz` route the readiness probe calls.
- `app.layout = layout`: assign the function, do not call it, so each page load
  builds the page fresh.
- Callbacks are plain functions with typed arguments and return values,
  decorated with `@callback(Output(...), Input(...))`. They hold no rules: call
  loaders, pass the frames to `app.logic`, shape the result for the component.
- Inputs arrive as JSON values: a date picker gives an ISO string, or `None`
  when cleared. Type them as such and `raise PreventUpdate` when there is
  nothing to show.
- Tables use `dash_ag_grid.AgGrid`. Never `dash_table.DataTable`: it is
  deprecated, and its warning fails the tests.
- Charts: `plotly.express` figures in `dcc.Graph`. Wrap slow outputs in
  `dcc.Loading`. Sections: `dcc.Tabs`.
- Caching is `@ctzd.cached` on loaders. Each gunicorn worker has its own cache
  and memory, so never keep per-user state in module globals; use `dcc.Store`.
- Errors raised in a callback leave that output empty for the person: the
  tests are what catch them, so test every callback.

## A page

`src/app/ui/main.py`

```python
import datetime as dt

import dash_ag_grid as dag
import pandas as pd
import plotly.express as px
from ctzd.dash import create_app
from dash import Input, Output, callback, dcc, html
from dash.exceptions import PreventUpdate
from plotly.graph_objects import Figure

from app.data.loaders import load_prices
from app.logic.prices import sector_summary

app = create_app(__name__, title="Bond Prices")
server = app.server

TWO_DECIMALS = {"function": "d3.format(',.2f')(params.value)"}
COLUMNS = [{"field": "sector"}, {"field": "bonds"}, {"field": "avg_price", "headerName": "Avg clean price", "valueFormatter": TWO_DECIMALS}]


def layout() -> html.Div:
    today = dt.datetime.now(dt.UTC).date()
    return html.Div(
        [
            html.H1("Bond prices by sector"),
            dcc.DatePickerSingle(id="as-of", date=today.isoformat()),
            dcc.Loading(dcc.Graph(id="chart")),
            dag.AgGrid(id="summary", columnDefs=COLUMNS, columnSize="sizeToFit", dashGridOptions={"pagination": True}),
        ]
    )


def _rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    return [{str(key): value for key, value in row.items()} for row in frame.to_dict("records")]


@callback(Output("summary", "rowData"), Output("chart", "figure"), Input("as-of", "date"))
def show_summary(as_of: str | None) -> tuple[list[dict[str, object]], Figure]:
    if as_of is None:
        raise PreventUpdate
    summary = sector_summary(load_prices(dt.date.fromisoformat(as_of)))
    return _rows(summary), px.bar(summary, x="sector", y="bonds")


app.layout = layout

if __name__ == "__main__":
    app.run(debug=True, port=8050)
```

Typing notes, for ty's strict mode:

- `frame.to_dict("records")` is typed with `Any` values, so returning it from
  a typed function fails `unsound-return-statement`. Convert it with a
  comprehension under a declared type, as `_rows` does. Passing it straight to
  `AgGrid(rowData=...)` inside `layout()` is fine.
- `dcc.DatePickerSingle(date=...)` takes a string: pass `day.isoformat()`.
- Component properties such as `grid.rowData` are not declared attributes;
  test the callback's return value instead of reading them back.

## Tests

Call `layout()` and each callback directly inside `ctzd.testing.fake_sources`.
Dash's own browser-test plugin is disabled (`-p no:dash`), so there are no
browser tests; the `/healthz` route is checked through Flask's test client.

`tests/test_ui.py`

```python
import ctzd
import pandas as pd
import pytest
from dash import html
from dash.exceptions import PreventUpdate

from app.ui.main import layout, server, show_summary


def prices() -> pd.DataFrame:
    return pd.DataFrame({"isin": ["XS0000000001", "XS0000000002", "XS0000000003"], "sector": ["Banks", "Utilities", "Banks"], "clean_price": [99.5, 101.25, 98.0]})


def test_layout_builds_with_a_title() -> None:
    page = layout()
    assert isinstance(page.children, list)
    assert isinstance(page.children[0], html.H1)


def test_summary_has_one_row_per_sector_most_bonds_first() -> None:
    with ctzd.testing.fake_sources(pricing_db={"sql/prices.sql": prices()}):
        rows, _figure = show_summary("2026-11-04")
    assert [(row["sector"], row["bonds"]) for row in rows] == [("Banks", 2), ("Utilities", 1)]


def test_a_cleared_date_leaves_the_page_as_it_is() -> None:
    with pytest.raises(PreventUpdate):
        show_summary(None)


def test_health_endpoint_answers() -> None:
    assert server.test_client().get("/healthz").status_code == 200
```

Locally `ctzd dev --json` serves the page at the `url` it returns
(`http://localhost:8501` unless that port is taken) with Dash's debug tools,
which show callback errors in the page.
