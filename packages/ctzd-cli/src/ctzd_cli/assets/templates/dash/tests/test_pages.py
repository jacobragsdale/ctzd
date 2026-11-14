"""Each page's callback, called on the sample rows, answers every output it names."""

import asyncio

from app.app import app
from app.pages import positions


def test_the_app_has_its_positions_page() -> None:
    assert app.title
    assert positions.layout() is not None


def test_the_positions_callback_answers_its_grid_its_stats_and_its_charts() -> None:
    shown = asyncio.run(positions.show(positions.last_close().isoformat(), None))
    rows, market_value, *_stats, sectors, scatter = shown
    assert isinstance(rows, list)
    assert rows
    assert str(market_value).startswith("$")
    assert sectors is not None
    assert scatter is not None


def test_the_curve_callback_answers_a_figure_from_the_apps_own_sql() -> None:
    assert asyncio.run(positions.show_curve(positions.last_close().isoformat())) is not None
