import pandas as pd
import pytest

import ctzd
from ctzd.notify import teams_card


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
