import datetime as dt
import io
from typing import Annotated

import openpyxl
import pandas as pd
import pytest

import ctzd
from ctzd.notify import Table, email_message, excel_format, teams_card


def test_email_attaches_frames_as_files() -> None:
    summary = pd.DataFrame({"sector": ["Gov"], "avg_price": [99.1]})
    with ctzd.testing.outbox() as sent:
        ctzd.notify.email("ann.lee@contoso.com", "Sector summary", "Attached.", attachments={"summary.xlsx": summary, "summary.csv": summary})
    (message,) = sent
    assert isinstance(message, ctzd.EmailMessage)
    assert message.to == ("ann.lee@contoso.com",)
    assert [item.filename for item in message.attachments] == ["summary.xlsx", "summary.csv"]
    assert message.attachments[1].content.decode().splitlines()[0] == "sector,avg_price"


def test_attachments_must_be_xlsx_or_csv() -> None:
    with ctzd.testing.outbox(), pytest.raises(ctzd.CtzdError, match=r"xlsx or \.csv"):
        ctzd.notify.email("a@contoso.com", "s", "b", attachments={"summary.pdf": pd.DataFrame()})


def test_teams_card_puts_the_title_first() -> None:
    card = teams_card(ctzd.TeamsMessage(source="fi_alerts", title="Done", text="3 bonds repriced"))
    assert card["type"] == "message"
    assert "Done" in str(card)
    assert "3 bonds repriced" in str(card)


def test_require_columns_accepts_matching_frames() -> None:
    prices = pd.DataFrame({"isin": ["X1"], "price": [1.0], "as_of": pd.to_datetime(["2026-11-04"])})
    assert ctzd.require_columns(prices, {"isin": "str", "price": "float", "as_of": "datetime"}) is prices


def test_require_columns_reports_every_problem_at_once() -> None:
    prices = pd.DataFrame({"price": ["1.0"]})
    with pytest.raises(ctzd.DataError) as caught:
        ctzd.require_columns(prices, {"isin": "str", "price": "float"}, what="prices")
    assert "missing column 'isin'" in str(caught.value)
    assert "column 'price'" in str(caught.value)


class SectorRow(ctzd.Row):
    sector: str
    bonds: ctzd.Count
    spread: ctzd.Bps
    weight: ctzd.Pct
    par: Annotated[float | None, ctzd.money("USD")]


def _summary() -> Table:
    frame = pd.DataFrame({"sector": ["Gov", "Corp"], "bonds": [3.0, 12.0], "spread": [0.00125, 0.0525], "weight": [0.4, 0.6], "par": [-1234.5, 1e6]})
    return Table(frame, SectorRow, title="Sector summary")


def test_a_table_attaches_as_a_workbook_that_keeps_numbers_raw_and_formats_them_by_kind() -> None:
    message = email_message("ann.lee@contoso.com", "Summary", "Attached.", attachments={"summary.xlsx": _summary()})
    sheet = openpyxl.load_workbook(io.BytesIO(message.attachments[0].content)).active
    assert sheet is not None
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:E3"
    assert [(cell.value, cell.number_format) for cell in sheet[2]] == [("Gov", "General"), (3, "#,##0"), (0.00125, "0.0000%"), (0.4, "0.00%"), (-1234.5, '"$"#,##0.00;-"$"#,##0.00')]


def test_a_table_in_the_body_reads_as_the_page_and_says_which_day_it_is_for() -> None:
    message = email_message("ann.lee@contoso.com", "Summary", "Today's summary.", table=_summary(), as_of=dt.date(2026, 11, 6), link="https://fi-bond-prices.ctzd.contoso.com")
    assert message.html is not None
    assert "12.50\u00a0bp" in message.html  # the page joins number and unit with a no-break space
    assert "-$1,234.50" in message.html
    assert "Data for 6 November 2026." in message.html
    assert 'href="https://fi-bond-prices.ctzd.contoso.com"' in message.html
    assert message.body == "Today's summary.\n\nData for 6 November 2026.\n\nSee it live: https://fi-bond-prices.ctzd.contoso.com"


def test_a_plain_email_has_no_html_body() -> None:
    assert email_message("ann.lee@contoso.com", "Hi", "Plain.").html is None


@pytest.mark.parametrize(("display", "expected"), [({"kind": "price"}, "#,##0.00"), ({"kind": "price32"}, "# ??/32"), ({"kind": "money", "currency": "JPY"}, '"¥"#,##0;-"¥"#,##0')])
def test_excel_formats_follow_the_page(display: dict[str, object], expected: str) -> None:
    assert excel_format(display) == expected
