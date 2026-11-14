"""The page driving's own rules: which callbacks a page load calls, in what order, how a cell reads, and which
callbacks read data outside `ui.load`. scripts/e2e.sh drives a real app end to end."""

import sys
import types

import pytest

from ctzd_cli.inapp import drive


def _entry(output: str, inputs: list[str], **more: object) -> dict[str, object]:
    return {"output": output, "inputs": [{"id": key.split(".")[0], "property": key.split(".")[1]} for key in inputs], "state": [], **more}


def test_a_callback_whose_input_another_answers_is_called_after_it() -> None:
    later = _entry("total.children", ["grid.rowData"])
    first = _entry("grid.rowData", ["as-of.value"])
    assert drive.ordered([later, first]) == [first, later]


def test_a_page_load_calls_neither_a_clientside_nor_a_prevented_nor_a_pattern_callback() -> None:
    on = {"as-of", "grid"}
    assert drive.called_on_load(_entry("grid.rowData", ["as-of.value"]), on)
    assert not drive.called_on_load(_entry("grid.rowData", ["as-of.value"], prevent_initial_call=True), on)
    assert not drive.called_on_load(_entry("grid.rowData", ["as-of.value"], clientside_function={"name": "x"}), on)
    assert not drive.called_on_load(_entry('{"type":"row","index":["ALL"]}.value', ["as-of.value"]), on)
    assert not drive.called_on_load(_entry("elsewhere.children", ["as-of.value"]), on)


def test_several_outputs_read_as_their_first_and_a_count() -> None:
    assert drive.outputs("..a.children...b.figure..") == [{"id": "a", "property": "children"}, {"id": "b", "property": "figure"}]
    assert drive.short("..a.children...b.figure..") == "a.children (and 1 more)"


@pytest.mark.parametrize(
    ("spec", "value", "shown"),
    [
        ({"decimals": 0, "symbol": "$"}, 1234567.5, "$1,234,568"),
        ({"decimals": 2, "percent": True}, 0.0525, "5.25%"),
        ({"decimals": 1, "bps": True}, 0.0525, "525.0\N{NO-BREAK SPACE}bp"),
        ({"thirtySeconds": 2}, 99.515625, "99-16+"),
        ({"decimals": 2}, 2.675, "2.68"),
        (None, "AAA111111", "AAA111111"),
    ],
)
def test_a_cell_reads_as_the_grid_shows_it(spec: dict[str, object] | None, value: object, shown: str) -> None:
    assert drive.shown_text(value, spec) == shown


def test_a_column_format_is_read_back_from_its_value_formatter() -> None:
    column = {"field": "par", "valueFormatter": {"function": 'ctzdFormat(params.value, {"decimals":0,"symbol":"$"})'}}
    assert drive._spec(column) == {"decimals": 0, "symbol": "$"}  # noqa: SLF001 - the parser is the unit under test
    assert drive._spec({"field": "name"}) is None  # noqa: SLF001


def test_a_figure_summary_names_its_traces_and_axes() -> None:
    figure = {"data": [{"type": "bar", "x": ["a", "b"], "y": [1, 2]}], "layout": {"yaxis": {"title": {"text": "Value"}, "tickformat": ",.0f"}}}
    assert drive.summary(None, "figure", figure) == {"kind": "figure", "traces": [{"type": "bar", "name": None, "points": 2}], "axes": {"yaxis": {"title": "Value", "format": ",.0f"}}}


def test_a_callback_that_reads_outside_ui_load_or_is_not_async_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    data = types.ModuleType("app.data")
    ui = types.ModuleType("app.ui")
    page = types.ModuleType("app.pages.example")
    source = (
        "def holdings(day):\n    return day\n\n"
        "async def good(day):\n    return await ui.load(holdings, day)\n\n"
        "def blocking(day):\n    return holdings(day)\n\n"
        "async def direct(day):\n    return data.holdings(day)\n"
    )
    exec(compile(source, "app/pages/example.py", "exec"), page.__dict__)  # noqa: S102 - a page module written here
    page.holdings.__module__ = "app.data"
    vars(page).update({"ui": ui, "data": data})
    monkeypatch.setitem(sys.modules, "app.pages.example", page)
    for name in ("good", "blocking", "direct"):
        getattr(page, name).__module__ = "app.pages.example"
    monkeypatch.setattr(drive.inspect, "getsource", lambda function: {"good": source.split("\n\n")[1], "blocking": source.split("\n\n")[2], "direct": source.split("\n\n")[3]}[function.__name__])
    assert drive.read_problems(page.good) == []
    blocking = drive.read_problems(page.blocking)
    assert [problem[0].split(" calls ")[0].split(" reads ")[0] for problem in blocking] == ["callback app.pages.example.blocking", "callback app.pages.example.blocking"]
    assert "not async" in blocking[1][0]
    direct = drive.read_problems(page.direct)
    assert len(direct) == 1
    assert "data.holdings()" in direct[0][0]


def test_a_page_with_nothing_to_choose_is_called_on_load_through_its_address() -> None:
    by_address = {"output": "today-total.children", "inputs": [{"id": "_pages_location", "property": "pathname"}], "state": []}
    assert drive.called_on_load(by_address, {"today-total"})
    assert drive.ordered([by_address]) == [by_address]
