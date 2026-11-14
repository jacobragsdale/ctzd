---
name: ctzd-pages
description: "Build and change the dashboard's Dash pages in a ctzd app: dash.register_page, the layout as a function, async callbacks that await ui.load, ui.py's formats, grids, charts, stats and cards, the loading, empty and failed states, ids as constants, and ctzd render to see a page as JSON with no browser. Use whenever you add, change or fix a page, a filter, a grid, a chart or a figure on the dashboard."
---

# Pages in a ctzd dashboard

A page is one module in `src/app/pages/`. Dash finds it by itself (`ui.dash_app` turns on Dash's pages), and the
firm's shell in `theme.py` puts a link to it in the title bar. Every page is built with `src/app/ui.py`, a locked
module: read its docstrings, which are the API. Never edit `ui.py`, `theme.py` or `__main__.py`, and never write a
grid formatter, a chart template or an error state of your own.

The template's `pages/positions.py` is a complete page: a date and a book filter, four stats, two charts, a grid and
a curve. Read it before you write one.

## Adding a page

1. **Data**: a function in `src/app/data/` that reads what the page needs through the kit (`ctzd-data`).
2. **Rules**: pure functions in `src/app/logic/`, frames in and a frame or a number out, with no reads and no Dash.
3. **The page**: `src/app/pages/NAME.py`, as below.
4. **A test** in `tests/`: each callback called on the sample rows (`ctzd-testing`), and tests of the new rules.
5. Look at it: `ctzd dev --fake --json`, then `ctzd render /PATH --json`. Then `ctzd check --json`.

When the person's dashboard is a single page, replace the template's `positions.py` (keep `path="/"`) rather than
adding a second one, and delete its `ctzd: sample` first line.

## A page

`src/app/pages/sectors.py`, beside the template's positions page, reusing its data and rules:

```python
"""Sectors: each sector's market value, DV01 and day's P&L for a book at a close."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import dash
import dash_mantine_components as dmc
from dash import Input, Output, callback

from app import ui
from app.data import holdings
from app.logic import positions as rules

dash.register_page(__name__, path="/sectors", title="Sectors", name="Sectors")

AS_OF = "sectors-as-of"
BOOK = "sectors-book"
TOTAL = "sectors-total"
CHART = "sectors-chart"
GRID = "sectors-grid"
BOOKS = ("Core Rates", "Credit", "Liquidity", "Municipals")
DESK = ZoneInfo("Europe/London")
SATURDAY = 5
USD = ui.money("USD", 0)
COLUMNS = [ui.column("sector", "Sector", pinned=True), ui.column("market_value", "Market value", USD), ui.column("dv01", "DV01", USD), ui.column("pnl", "Day P&L", USD)]


def last_close() -> date:
    day = datetime.now(DESK).date() - timedelta(days=1)
    while day.weekday() >= SATURDAY:
        day -= timedelta(days=1)
    return day


def layout() -> dmc.Stack:
    filters = dmc.Group(
        [
            dmc.DateInput(id=AS_OF, label="As of", value=last_close().isoformat(), valueFormat="D MMM YYYY", w=180),
            dmc.Select(id=BOOK, label="Book", data=list(BOOKS), placeholder="All books", clearable=True, w=200),
        ]
    )
    return dmc.Stack([filters, ui.stat(TOTAL, "Market value"), ui.card("By sector", ui.graph(CHART)), ui.card("Sectors", ui.grid(GRID, COLUMNS, height=320))], gap="md")


@callback(Output(TOTAL, "children"), Output(CHART, "figure"), Output(GRID, "rowData"), Input(AS_OF, "value"), Input(BOOK, "value"))
async def show(as_of: str, book: str | None) -> tuple[object, ...]:
    held = await ui.load(holdings, date.fromisoformat(as_of))
    shown = rules.positions(held, book or "")
    sectors = rules.by_sector(shown)
    return ui.text(rules.totals(shown)["market_value"], USD), ui.bar(sectors, x="sector", y="market_value", y_format=USD), ui.rows(sectors)
```

What every page does:

- **`dash.register_page(__name__, path=..., title=..., name=...)`** at the top. `path` is the address (`/` for the
  first page), `name` the link in the title bar.
- **The layout is a function**, `def layout() -> ...`, so it is built afresh on each visit (a date default such as
  the last close is then current). It builds components only: it never reads data. Data arrives through callbacks.
- **Ids are module constants**, prefixed with the page's name (`sectors-book`), because every id must be unique
  across the dashboard. Write each id once, as a constant, and use the constant in the layout and the callbacks.
- **Every callback's ids name a component its page has**, with a property that component has: `Output(GRID,
  "rowData")` for a grid, `Output(CHART, "figure")` for a graph, `Output(STAT, "children")` for a stat,
  `Input(SELECT, "value")` for a filter. `ctzd check` drives each page and refuses a callback wired to nothing.
- **A callback that reads data is `async def` and awaits `ui.load(data_function, *args)`**, which runs the `data/`
  function in a worker thread, so a slow read never holds the server for everyone else, and shows the sample-data
  banner when the rows came from sample rows. Never call a `data/` function directly in a callback, and never read
  in a plain `def` callback: `ctzd check` refuses both. Several reads are several `await ui.load(...)` lines.
- **Rules go in `logic/`**: the callback loads, calls the rules and returns what `ui.py` builds. Keep it that short.
- **One callback per thing that changes for a different reason**: the template gives the curve its own callback,
  because it depends only on the date.

## A page with nothing to choose

A page with no filter still gets its data through a callback: the layout never reads. Give the callback the page's
own address as its one input, `Input("_pages_location", "pathname")`, a component Dash Pages puts on every page. Dash
calls the callback once when the page opens, as it does any other, and `ctzd check` and `ctzd render` drive it the
same way. The argument is the page's path, which the callback need not use: name it with a leading underscore.

```python
"""Today: the book's totals and its sectors at the last close, with nothing for the person to choose."""

import dash
import dash_mantine_components as dmc
from dash import Input, Output, callback

from app import ui
from app.data import holdings
from app.logic import positions as rules
from app.pages.positions import last_close

dash.register_page(__name__, path="/today", title="Today", name="Today")

LOCATION = "_pages_location"
"""Dash Pages' own component, on every page: its `pathname` is the page's address."""
TOTAL = "today-total"
GRID = "today-grid"
USD = ui.money("USD", 0)
COLUMNS = [ui.column("sector", "Sector", pinned=True), ui.column("market_value", "Market value", USD), ui.column("pnl", "Day P&L", USD)]


def layout() -> dmc.Stack:
    return dmc.Stack([ui.stat(TOTAL, "Market value"), ui.card("Sectors", ui.grid(GRID, COLUMNS, height=320))], gap="md")


@callback(Output(TOTAL, "children"), Output(GRID, "rowData"), Input(LOCATION, "pathname"))
async def show(_path: str) -> tuple[object, ...]:
    shown = rules.positions(await ui.load(holdings, last_close()), "")
    return ui.text(rules.totals(shown)["market_value"], USD), ui.rows(rules.by_sector(shown))
```

Never trigger a callback from a component's `id` or from a hidden component made for the purpose: the address is
the one input every page has.

## Formats

A page names a format for every number it shows, in a grid column, a stat or a chart axis, and never writes one.
They round by the firm's one rule (`ctzd_common.display`), so a grid, a chart's hover, a stat, an email and Excel
show a value alike. Rates and spreads are fractions: 0.0525 shows as 5.25% and as 525.0 bp.

| Format | Shows | Example |
|---|---|---|
| `ui.number(2)` | a plain number, to the decimals given (default 2) | `1,234.50` |
| `ui.count()` | a whole number | `42` |
| `ui.money("USD", 0)` | an amount in an ISO currency, to its minor units unless decimals are given | `$1,235` |
| `ui.pct(2)` | a fraction as a percentage | `5.25%` |
| `ui.bps(1)` | a fraction in basis points | `525.0 bp` |
| `ui.price(3)` | a price per 100 | `101.250` |
| `ui.price32()` | a Treasury price in 32nds; `ui.price32("half")`, `"quarter"`, `"eighth"` for finer steps | `99-16+` |

Name a format once as a constant (`USD = ui.money("USD", 0)`) when several columns share it. Text and date columns
take no format: `ui.rows` sends dates as ISO text.

## The building blocks

| In the layout | Its callback answers | With |
|---|---|---|
| `ui.grid(ID, COLUMNS, height=480)` | `Output(ID, "rowData")` | `ui.rows(frame)` |
| `ui.column(FIELD, HEADER, FORMAT, width=None, pinned=False)` | one entry of `COLUMNS` | `HEADER` defaults to the field, capitalised; `FORMAT` only for numbers |
| `ui.graph(ID, height=360)` | `Output(ID, "figure")` | `ui.bar`, `ui.line`, `ui.scatter`, or `ui.empty(SENTENCE)` |
| `ui.stat(ID, LABEL)` | `Output(ID, "children")` | `ui.text(VALUE, FORMAT)` |
| `ui.card(TITLE, CHILD)` | | a titled panel around a grid, a graph or anything else |

Charts, each built from a frame, in the firm's template, each axis in its format:

- `ui.bar(frame, x="sector", y="market_value", y_format=USD, color=None)`: one bar per row; `color` names a column
  that splits the bars into series.
- `ui.line(frame, x="years", y="ytw", y_format=ui.pct(2), color=None)`: a line through the rows in order of `x`.
- `ui.scatter(frame, x="duration", y="ytw", x_format=ui.number(2), y_format=ui.pct(2), label="cusip", color="sector")`:
  one point per row, `label` named on hover.

Every argument after the frame is a keyword. An empty frame gives a chart that says "Nothing to show for this view".

Lay the page out with `dmc.Stack` (a column), `dmc.Group` (a row of filters) and `dmc.SimpleGrid(cols=N)` (tiles or
charts side by side), and filters with `dmc.DateInput`, `dmc.Select`, `dmc.MultiSelect`, `dmc.SegmentedControl`
and `dmc.Switch`. Keep to dash-mantine-components, dash-ag-grid, Plotly through `ui.py`, and Dash's own `dcc` and
`html`.

## The states

They come from `ui.py`; a page writes none of them.

- **Loading**: a grid, a graph or a stat dims with a thin bar once its answer takes 400 ms.
- **Empty**: an empty grid says "No rows for this view"; an empty chart says "Nothing to show for this view". Use
  `ui.empty("The first refresh runs at 07:00; the figures appear after it.")` to say why.
- **Failed**: when a callback raises, every output keeps what it showed, and the person sees a notification.
  `ui.on_error`, which `ui.dash_app` gives Dash, is the one handler of every callback's error. So never catch an
  error to return an empty frame or a message: let it raise. A kit's own errors already say what to do.
- **Sample data**: when a load served sample rows, the page shows the sample-data banner by itself.

## Seeing a page: `ctzd render`

There is no browser here. `ctzd render PAGE --json` builds the page on the sample rows, runs every callback its load
calls in the browser's order, and prints what a person would see. PAGE is the page's path or its name: `/`,
`/sectors` or `Sectors`.

```text
ctzd render / --json
ctzd render /sectors --set sectors-book.value=Credit --json
```

`--set ID.PROP=VALUE` gives a component a value in place of its layout's, as if the person had picked it, so you
can see each filter work. VALUE is JSON or plain text.

What it prints, besides `ok`, `summary` and `next`:

- `outputs`, by `ID.PROP`: a grid's `rows` count, its `columns` (headers) and its `first` rows as the grid displays
  them, numbers formatted; a figure's `traces` (type, name, points) and `axes` (title, format), and what an empty
  figure `says`; a stat's or a text's `text`;
- `failed`: each callback that failed, and its error;
- `notifications`: what the person would be told; `sample_data`: whether the sample-data banner shows; `loads`:
  each `ui.load`, how long it took, and whether it served sample rows.

Run it after every change to a page and read it: are these the numbers asked for, in the right format, with nothing
failed? It is not a gate, but `ctzd check` drives every page the same way and fails what it finds.

Then give the person the `url` from `ctzd dev --json` (on real data) or `ctzd dev --fake --json` (on sample rows),
and ask whether it shows what they need.

## When a check fails on a page

| `ctzd check` says | Fix |
|---|---|
| a callback names an id or property its page does not have | Use the constant, and the property of that component (`rowData`, `figure`, `children`, `value`). |
| a callback reads data without `ui.load`, or is not `async` | `async def`, and `await ui.load(data_function, ...)`. |
| a callback failed on the page's load | `ctzd render PAGE --json` shows the error; usually a column name the rules expect that the rows do not have. |
| a callback held the server's event loop | Something slow runs outside `ui.load`: move the read into `data/` and load it. When the rules themselves are slow on real data, a job computes them on a schedule and writes a table the page reads (`ctzd-jobs`). |
| a callback answered more than the budget | Send fewer rows or columns, or a summary: a grid of every trade is not a page. |
| rows that hold no `COLUMN`, a column of the grid | The frame given to `ui.rows` lacks a field a `ui.column` names: rename the column or the field. |
| a figure Plotly refuses | Build figures with `ui.bar`, `ui.line` or `ui.scatter`. |
| `layers`: `app.pages... is not allowed to import ...` | A page imports the kit: put the read in `data/`. |
| `numbers` | Rounding or a number format in a page: name a `ui.py` format instead. |
