# ctzd: sample page. Replace it with the page the person asked for, then delete this line.
"""Positions: every holding of the book asked for, as of a close, with its totals, its sectors, its yield against
duration and the Treasury curve."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import dash
import dash_mantine_components as dmc
from dash import Input, Output, callback

from app import ui
from app.data import curve, holdings
from app.logic import positions as rules

dash.register_page(__name__, path="/", title="Positions", name="Positions")

AS_OF = "positions-as-of"
BOOK = "positions-book"
MARKET_VALUE = "positions-market-value"
DV01 = "positions-dv01"
PNL = "positions-pnl"
YIELD = "positions-yield"
SECTORS = "positions-sectors"
SCATTER = "positions-scatter"
CURVE = "positions-curve"
GRID = "positions-grid"
BOOKS = ("Core Rates", "Credit", "Liquidity", "Municipals")
DESK = ZoneInfo("Europe/London")
SATURDAY = 5
USD = ui.money("USD", 0)
COLUMNS = [
    ui.column("cusip", "CUSIP", pinned=True, width=120),
    ui.column("book"),
    ui.column("issuer", width=190),
    ui.column("sector"),
    ui.column("rating"),
    ui.column("coupon", "Coupon", ui.pct(3)),
    ui.column("maturity"),
    ui.column("par", "Par", USD),
    ui.column("price", "Price", ui.price(3)),
    ui.column("market_value", "Market value", USD),
    ui.column("weight", "Weight", ui.pct(2)),
    ui.column("ytw", "YTW", ui.pct(3)),
    ui.column("duration", "Duration", ui.number(2)),
    ui.column("dv01", "DV01", USD),
    ui.column("pnl", "Day P&L", USD),
]


def last_close() -> date:
    """The last weekday before today in London, the close a desk reads in the morning."""
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
    stats = dmc.SimpleGrid([ui.stat(MARKET_VALUE, "Market value"), ui.stat(DV01, "DV01"), ui.stat(PNL, "Day P&L"), ui.stat(YIELD, "Yield to worst")], cols=4)
    charts = dmc.SimpleGrid([ui.card("Market value by sector", ui.graph(SECTORS)), ui.card("Yield against duration", ui.graph(SCATTER))], cols=2)
    return dmc.Stack([filters, stats, charts, ui.card("Holdings", ui.grid(GRID, COLUMNS)), ui.card("Treasury curve", ui.graph(CURVE, height=280))], gap="md")


@callback(
    Output(GRID, "rowData"),
    Output(MARKET_VALUE, "children"),
    Output(DV01, "children"),
    Output(PNL, "children"),
    Output(YIELD, "children"),
    Output(SECTORS, "figure"),
    Output(SCATTER, "figure"),
    Input(AS_OF, "value"),
    Input(BOOK, "value"),
)
async def show(as_of: str, book: str | None) -> tuple[object, ...]:
    held = await ui.load(holdings, date.fromisoformat(as_of))
    shown = rules.positions(held, book or "")
    total = rules.totals(shown)
    return (
        ui.rows(shown),
        ui.text(total["market_value"], USD),
        ui.text(total["dv01"], USD),
        ui.text(total["pnl"], USD),
        ui.text(total["ytw"], ui.pct(2)),
        ui.bar(rules.by_sector(shown), x="sector", y="market_value", y_format=USD),
        ui.scatter(shown, x="duration", y="ytw", x_format=ui.number(2), y_format=ui.pct(2), label="cusip", color="sector"),
    )


@callback(Output(CURVE, "figure"), Input(AS_OF, "value"))
async def show_curve(as_of: str) -> object:
    points = await ui.load(curve, date.fromisoformat(as_of))
    return ui.line(points, x="years", y="ytw", y_format=ui.pct(2))
