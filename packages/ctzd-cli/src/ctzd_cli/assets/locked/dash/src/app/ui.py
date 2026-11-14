"""The firm's grids, charts, states and loads, which every page builds with. Platform-owned: ctzd sync rewrites this
file, so change a page, never this.

- **Formats**: `number`, `count`, `money`, `pct`, `bps`, `price`, `price32`. A page names one for each number column and
  axis and never writes one: the grid, a chart's axes and hover text, a stat and an email all round by
  `ctzd_common.display`'s one rule.
- **Grids**: `column(FIELD, HEADER, FORMAT)` for each column, `grid(ID, COLUMNS)` for the grid, and `rows(frame)` for
  what a callback answers to its `rowData`.
- **Charts**: `graph(ID)` in the layout; `bar`, `line` and `scatter` build the figure a callback answers, in the
  firm's template, with each axis in its format; `empty(SENTENCE)` for nothing to show.
- **Tiles and cards**: `stat(ID, LABEL)`, whose `children` a callback answers with `text(VALUE, FORMAT)`;
  `card(TITLE, CHILD)`.
- **States**: a grid or a graph shows that it is loading once an answer takes 400 ms; an empty grid says so; a failed
  callback keeps what each output showed and tells the person in the notifications (`on_error`, the one handler of every
  callback's error, which `dash_app` gives Dash).
- **Loads**: `await load(read, *args)` in an `async` callback runs one of the app's `data/` functions in a worker
  thread, so a slow query never holds the server for everyone else, and shows the sample-data banner when what it read
  came from sample rows. `ctzd check` refuses a callback that reads any other way.
"""

import functools
import json
import logging
import math
import time
from collections.abc import Callable, Sequence
from typing import Final

import anyio.to_thread
import dash
import dash_ag_grid as dag
import dash_mantine_components as dmc
import pandas as pd
import plotly.graph_objects as go
from ctzd_common import display, samples
from ctzd_common.display import Format
from dash import _dash_renderer, dcc, html
from dash.development.base_component import Component
from dash.exceptions import MissingCallbackContextException

from app import theme

_LOG = logging.getLogger("ctzd.ui")
REACT: Final = "19.2.4"
"""The React Dash 4.5 opts into, which Mantine 9 needs."""
LOADING_MS: Final = 400
EMPTY_ROWS: Final = "No rows for this view"
FAILED: Final = "Something went wrong."
SUPPORT: Final = "This shows what it showed before. Tell the app's support team."

_dash_renderer._set_react_version(REACT, REACT)  # noqa: SLF001 - Dash's opt-in to React 19 has no public name

# ---------------------------------------------------------------- formats


def number(decimals: int = 2) -> Format:
    return Format("number", decimals)


def count() -> Format:
    return Format("number", 0)


def money(currency: str, decimals: int | None = None) -> Format:
    """An amount in `currency`, ISO 4217's code, to its minor units unless `decimals` says."""
    return Format("money", decimals, currency=currency)


def pct(decimals: int = 2) -> Format:
    """A fraction as a percentage: 0.0525 is 5.25%."""
    return Format("pct", decimals)


def bps(decimals: int = 1) -> Format:
    """A fraction in basis points: 0.0525 is 525.0 bp."""
    return Format("bps", decimals)


def price(decimals: int = 3) -> Format:
    return Format("price", decimals)


def price32(step: display.Step = "32nd") -> Format:
    return Format("price32", step=step)


def text(value: float | None, given: Format) -> str:
    """A value as a stat, a title or a hover shows it."""
    return display.text(value, given)


SCRIPT: Final = r"""
(function () {
  "use strict";
  var SIGNIFICANT = 15, PRICE_DECIMALS = 8, EIGHTHS = 256n, BP = "\u00a0bp";
  function power(e) { return 10n ** BigInt(e); }
  function halfAway(digits, exponent, decimals) {
    var shift = exponent + decimals;
    if (shift >= 0) return digits * power(shift);
    var divisor = power(-shift), quotient = digits / divisor;
    return 2n * (digits % divisor) >= divisor ? quotient + 1n : quotient;
  }
  function fifteen(magnitude) {
    var view = new DataView(new ArrayBuffer(8));
    view.setFloat64(0, magnitude);
    var high = view.getUint32(0), biased = (high >>> 20) & 0x7ff;
    var mantissa = (BigInt(high & 0xfffff) << 32n) | BigInt(view.getUint32(4)), binary = -1074;
    if (biased !== 0) { mantissa |= 1n << 52n; binary = biased - 1075; }
    var exact = binary >= 0 ? mantissa << BigInt(binary) : mantissa * 5n ** BigInt(-binary);
    var scale = binary >= 0 ? 0 : -binary, dropped = exact.toString().length - SIGNIFICANT;
    if (dropped <= 0) return [exact, -scale];
    return [halfAway(exact, -dropped, 0), dropped - scale];
  }
  function shortest(magnitude) {
    var parts = magnitude.toExponential().split("e"), mantissa = parts[0].split("."), fraction = mantissa[1] || "";
    return [BigInt(mantissa[0] + fraction), Number(parts[1]) - fraction.length];
  }
  function grouped(whole) { return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function format(value, spec) {
    spec = spec || {};
    if (value === null || value === undefined || value === "") return "";
    if (typeof value !== "number") return String(value);
    if (Number.isNaN(value)) return "";
    var negative = value < 0 || Object.is(value, -0), magnitude = Math.abs(value);
    if (!Number.isFinite(value)) return (negative ? "-" : "") + "\u221e";
    if (spec.thirtySeconds) {
      var short = shortest(magnitude), per = BigInt(32 * spec.thirtySeconds);
      var units = halfAway(halfAway(short[0], short[1], PRICE_DECIMALS) * per, -PRICE_DECIMALS, 0) * (EIGHTHS / per);
      var rest = units % EIGHTHS, eighth = rest % 8n, mark = eighth === 0n ? "" : eighth === 4n ? "+" : eighth.toString();
      var body32 = (units / EIGHTHS).toString() + "-" + (rest / 8n).toString().padStart(2, "0") + mark;
      return (negative && units !== 0n ? "-" : "") + body32;
    }
    var found = value === 0 ? [0n, 0] : fifteen(magnitude), decimals = spec.decimals === undefined ? 2 : spec.decimals;
    var rounded = halfAway(found[0], found[1] + (spec.percent ? 2 : 0) + (spec.bps ? 4 : 0), decimals), scale = power(decimals);
    var body = grouped((rounded / scale).toString());
    if (decimals > 0) body += "." + (rounded % scale).toString().padStart(decimals, "0");
    body = (spec.symbol || "") + body + (spec.percent ? "%" : "") + (spec.bps ? BP : "");
    return (negative && rounded !== 0n ? "-" : "") + body;
  }
  var functions = (window.dashAgGridFunctions = window.dashAgGridFunctions || {});
  functions.ctzdFormat = format;
})();
"""
"""The browser's half of `ctzd_common.display`, a port of basis-ui's formatter, so a grid's cell rounds as the server,
an email and Excel do. `theme.index_string` puts it in the page."""

# ---------------------------------------------------------------- grids


def column(field: str, header: str | None = None, given: Format | None = None, *, width: int | None = None, pinned: bool = False) -> dict[str, object]:
    """One column of a grid: `field`, the frame's column, under `header`, its numbers in the format `given`."""
    found: dict[str, object] = {"field": field, "headerName": header or field.replace("_", " ").capitalize()}
    if given is not None:
        found |= {"valueFormatter": {"function": f"ctzdFormat(params.value, {json.dumps(given.spec(), separators=(',', ':'))})"}, "type": "rightAligned", "filter": "agNumberColumnFilter"}
    if width is not None:
        found["width"] = width
    if pinned:
        found["pinned"] = "left"
    return found


def _loading(child: Component, of: str, props: Sequence[str]) -> dcc.Loading:
    """`child`, dimmed with a thin bar along its top once an answer to `of`'s `props` takes 400 ms, so a quick answer
    never flickers."""
    return dcc.Loading(child, delay_show=LOADING_MS, delay_hide=0, show_initially=False, overlay_style={"visibility": "visible", "opacity": 0.55}, target_components={of: list(props)}, type="dot")


def grid(grid_id: str, columns: Sequence[dict[str, object]], *, height: int = 480) -> dcc.Loading:
    """A grid in the firm's theme, its rows answered by a callback with `rows(frame)`."""
    table = dag.AgGrid(
        id=grid_id,
        columnDefs=list(columns),
        rowData=[],
        defaultColDef={"sortable": True, "resizable": True, "filter": True},
        dashGridOptions={"theme": theme.GRID, "overlayNoRowsTemplate": EMPTY_ROWS, "animateRows": False},
        style={"height": f"{height}px", "width": "100%"},
    )
    return _loading(table, grid_id, ["rowData"])


def _cell(value: object) -> object:
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat() if value == value.normalize() else value.isoformat()
    return value


def rows(frame: pd.DataFrame) -> list[dict[str, object]]:
    """A frame as a grid's `rowData`: one record per row, every number as it is, a missing value as null, a date as
    ISO text."""
    return [{str(key): _cell(value) for key, value in record.items()} for record in frame.to_dict("records")]


# ---------------------------------------------------------------- charts


def _d3(given: Format) -> tuple[str, float]:
    """An axis's tick and hover format for Plotly, which formats in the browser, and what to multiply the values by."""
    if given.kind == "pct":
        return f".{given.shown_decimals}%", 1.0
    if given.kind == "bps":
        return f",.{given.shown_decimals}f", 10_000.0
    prefix = display.CURRENCIES[given.currency][0] if given.kind == "money" and given.currency is not None else ""
    return f"{prefix},.{given.shown_decimals}f", 1.0


def _axis(given: Format, title: str) -> tuple[dict[str, object], float, str]:
    shape, scale = _d3(given)
    suffix = " (bp)" if given.kind == "bps" else ""
    return {"tickformat": shape, "title": {"text": title + suffix}}, scale, shape


def graph(graph_id: str, *, height: int = 360) -> dcc.Loading:
    """A chart, its figure answered by a callback with `bar`, `line` or `scatter`."""
    shown = dcc.Graph(id=graph_id, figure=empty(""), config={"displaylogo": False, "displayModeBar": "hover"}, style={"height": f"{height}px"})
    return _loading(shown, graph_id, ["figure"])


def empty(sentence: str) -> go.Figure:
    """A figure with nothing to plot, saying why."""
    figure = go.Figure()
    figure.update_layout(xaxis={"visible": False}, yaxis={"visible": False}, annotations=[{"text": sentence, "showarrow": False, "font": {"size": 13}}] if sentence else [])
    return figure


def _labelled(column: str) -> str:
    return column.replace("_", " ").capitalize()


def bar(frame: pd.DataFrame, *, x: str, y: str, y_format: Format, color: str | None = None) -> go.Figure:
    """One bar for each row: `x` along the bottom, `y` in `y_format`, one series for each value of `color`."""
    if frame.empty:
        return empty("Nothing to show for this view")
    yaxis, scale, shape = _axis(y_format, _labelled(y))
    groups = [(None, frame)] if color is None else list(frame.groupby(color, sort=False))
    figure = go.Figure([go.Bar(x=part[x], y=part[y] * scale, name=None if name is None else str(name), hovertemplate=f"%{{x}}: %{{y:{shape}}}<extra></extra>") for name, part in groups])
    figure.update_layout(yaxis=yaxis, xaxis={"title": {"text": _labelled(x)}}, showlegend=color is not None)
    return figure


def line(frame: pd.DataFrame, *, x: str, y: str, y_format: Format, color: str | None = None) -> go.Figure:
    """A line through the rows in order of `x`, `y` in `y_format`, one for each value of `color`."""
    if frame.empty:
        return empty("Nothing to show for this view")
    yaxis, scale, shape = _axis(y_format, _labelled(y))
    groups = [(None, frame)] if color is None else list(frame.groupby(color, sort=False))
    figure = go.Figure(
        [go.Scatter(x=part[x], y=part[y] * scale, mode="lines+markers", name=None if name is None else str(name), hovertemplate=f"%{{x}}: %{{y:{shape}}}<extra></extra>") for name, part in groups]
    )
    figure.update_layout(yaxis=yaxis, xaxis={"title": {"text": _labelled(x)}}, showlegend=color is not None)
    return figure


def scatter(frame: pd.DataFrame, *, x: str, y: str, x_format: Format, y_format: Format, label: str | None = None, color: str | None = None) -> go.Figure:  # noqa: PLR0913 - each is keyword-only and named for what it sets
    """One point for each row, `x` and `y` in their formats, named by `label` on hover, one series for each `color`."""
    if frame.empty:
        return empty("Nothing to show for this view")
    xaxis, x_scale, x_shape = _axis(x_format, _labelled(x))
    yaxis, y_scale, y_shape = _axis(y_format, _labelled(y))
    groups = [(None, frame)] if color is None else list(frame.groupby(color, sort=False))
    hover = ("%{text}: " if label is not None else "") + f"%{{x:{x_shape}}}, %{{y:{y_shape}}}<extra></extra>"
    figure = go.Figure(
        [
            go.Scatter(x=part[x] * x_scale, y=part[y] * y_scale, text=None if label is None else part[label], mode="markers", name=None if name is None else str(name), hovertemplate=hover)
            for name, part in groups
        ]
    )
    figure.update_layout(xaxis=xaxis, yaxis=yaxis, showlegend=color is not None)
    return figure


# ---------------------------------------------------------------- tiles and cards


def stat(stat_id: str, label: str) -> dmc.Paper:
    """A figure people read at a glance: `label`, and the value a callback answers to `ID.children` with `text`."""
    return dmc.Paper(
        [dmc.Text(label, size="sm", c="dimmed"), _loading(dmc.Text("\N{EM DASH}", id=stat_id, size="xl", fw=600, className="ctzd-number"), stat_id, ["children"])], withBorder=True, p="md", radius="md"
    )


def card(title: str, child: Component) -> dmc.Paper:
    return dmc.Paper([dmc.Text(title, fw=600, mb="sm"), html.Div(child)], withBorder=True, p="md", radius="md")


# ---------------------------------------------------------------- loads and errors


def _in_callback() -> bool:
    try:
        return dash.callback_context.triggered_prop_ids is not None
    except MissingCallbackContextException:
        return False


async def load[**P, T](read: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """`read(*args, **kwargs)`, one of the app's `data/` functions, in a worker thread, so the server keeps answering
    everyone else meanwhile; and the sample-data banner shown when what it read came from sample rows."""
    started = time.perf_counter()
    with samples.watching() as served:
        found = await anyio.to_thread.run_sync(functools.partial(read, *args, **kwargs))
    name = getattr(read, "__name__", "read")
    _LOG.info("loaded %s in %.0f ms%s", name, (time.perf_counter() - started) * 1000, f" from sample rows of {', '.join(sorted(served))}" if served else "")
    if served and _in_callback():
        dash.set_props(theme.SAMPLE_BANNER, {"style": {"display": "block"}})
    return found


def _sentence(error: Exception) -> tuple[str, str]:
    """What happened, in a sentence a person can act on, and what to do. A kit's own errors already end with the fix."""
    if any(kind.__name__ == "KitError" for kind in type(error).__mro__):
        return str(error), "This shows what it showed before."
    return FAILED, SUPPORT


def on_error(error: Exception) -> None:
    """The one handler of every callback's error: logged once, told to the person, and every output left as it was."""
    _LOG.error("a callback failed: %s: %s", type(error).__name__, error, exc_info=error)
    if _in_callback():
        title, then = _sentence(error)
        note = {"id": f"ctzd-error-{type(error).__name__}", "title": title, "message": then, "color": "red", "autoClose": False, "withCloseButton": True, "action": "show"}
        dash.set_props(theme.NOTIFICATIONS, {"sendNotifications": [note]})


def dash_app(title: str) -> dash.Dash:
    """The Dash app, with every setting given here so no `DASH_*` variable can change one: the FastAPI backend, the
    pages in `pages/`, the firm's page around them, and `on_error`."""
    return dash.Dash(
        "app",
        backend="fastapi",
        use_pages=True,
        pages_folder="pages",
        title=title,
        update_title="",
        index_string=theme.index_string(SCRIPT),
        on_error=on_error,
        serve_locally=True,
        compress=False,
        suppress_callback_exceptions=False,
        enable_mcp=False,
        shared_storage=None,
        include_assets_files=False,
    )
