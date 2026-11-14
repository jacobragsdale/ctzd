"""An email's plain and HTML bodies, its attachments, and where it goes in each environment."""

import io
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Self

import openpyxl
import pandas as pd
import pytest

from ctzd_common import display, email, messages, samples
from ctzd_common.display import Format
from ctzd_common.email import EmailError, Table

NBSP = "\N{NO-BREAK SPACE}"
FORMATS = {"bonds": Format("number", 0), "spread": Format("bps"), "weight": Format("pct"), "par": Format("money", currency="USD")}


@pytest.fixture(autouse=True)
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop, in the test's own folder, with no message settings from the environment."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    samples.reset()


def summary() -> Table:
    frame = pd.DataFrame(
        {
            "sector": ["Gov", "Corp & <Other>"],
            "bonds": [3.0, 12.0],
            "spread": [0.00125, 0.0525],
            "weight": [0.4, 0.6],
            "par": [-1234.5, 1e6],
            "as_of": [pd.Timestamp("2026-11-13"), pd.Timestamp("2026-11-13 09:30")],
            "closed": [True, False],
        }
    )
    return Table(frame, FORMATS, title="Sector summary")


def html_of(mime: EmailMessage) -> str:
    body = mime.get_body(preferencelist=("html",))
    assert body is not None
    content = body.get_content()
    assert isinstance(content, str)
    return content


def plain_of(mime: EmailMessage) -> str:
    body = mime.get_body(preferencelist=("plain",))
    assert body is not None
    content = body.get_content()
    assert isinstance(content, str)
    return content


def test_a_table_in_the_body_reads_as_the_page_writes_it() -> None:
    mime = email.message(["ann.lee@contoso.com", "bo.chan@contoso.com"], "Summary", "Today's summary.\n\nSecond line.", table=summary(), link="https://fi-bond-prices.ctzd.contoso.com")
    assert mime["To"] == "ann.lee@contoso.com, bo.chan@contoso.com"
    assert mime["From"] == "ctzd@contoso.com"
    html = html_of(mime)
    assert '<caption style="text-align: left; font-weight: bold">Sector summary</caption>' in html
    for shown in (f"12.50{NBSP}bp", "-$1,234.50", "$1,000,000.00", "40.00%", ">12<", "2026-11-13 09:30", ">2026-11-13<", ">Yes<", ">No<"):
        assert shown in html, shown
    assert "Corp &amp; &lt;Other&gt;" in html
    assert "<p>Today&#x27;s summary.</p><p>Second line.</p>" in html
    assert 'href="https://fi-bond-prices.ctzd.contoso.com"' in html
    assert 'text-align: right">3<' in html, "numbers are right-aligned"
    assert plain_of(mime).strip() == "Today's summary.\n\nSecond line.\n\nSee it live: https://fi-bond-prices.ctzd.contoso.com"


def test_a_plain_email_has_no_html_body() -> None:
    mime = email.message("ann.lee@contoso.com", "Hi", "Plain.")
    assert mime.get_body(preferencelist=("html",)) is None
    assert plain_of(mime).strip() == "Plain."


def test_an_email_needs_a_recipient() -> None:
    with pytest.raises(EmailError, match="at least one recipient"):
        email.message([], "Hi", "Plain.")


def test_a_table_attaches_as_a_workbook_that_keeps_numbers_raw_and_formats_them() -> None:
    found = email.attachment("summary.xlsx", summary())
    assert found.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    sheet = openpyxl.load_workbook(io.BytesIO(found.content)).active
    assert sheet is not None
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:G3"
    row = [(cell.value, cell.number_format) for cell in sheet[2]]
    assert row[:5] == [("Gov", "General"), (3, "#,##0"), (0.00125, "0.0000%"), (0.4, "0.00%"), (-1234.5, '"$"#,##0.00;-"$"#,##0.00')]
    assert row[5][1] == "yyyy-mm-dd"
    assert row[6][0] is True


def test_a_plain_frame_attaches_as_a_workbook_or_csv() -> None:
    frame = pd.DataFrame({"sector": ["Gov"], "avg_price": [99.1]})
    sheet = openpyxl.load_workbook(io.BytesIO(email.attachment("summary.xlsx", frame).content)).active
    assert sheet is not None
    assert [cell.value for cell in sheet[1]] == ["sector", "avg_price"]
    as_csv = email.attachment("summary.csv", Table(frame))
    assert as_csv.mime_type == "text/csv"
    assert as_csv.content.decode().splitlines() == ["sector,avg_price", "Gov,99.1"]


def test_an_empty_frame_attaches_as_a_workbook() -> None:
    sheet = openpyxl.load_workbook(io.BytesIO(email.attachment("empty.xlsx", pd.DataFrame()).content)).active
    assert sheet is not None


def test_attachments_must_be_xlsx_or_csv() -> None:
    with pytest.raises(EmailError, match=r"\.xlsx or \.csv"):
        email.attachment("summary.pdf", pd.DataFrame())


def test_attachments_ride_along_with_the_message() -> None:
    frame = pd.DataFrame({"sector": ["Gov"]})
    mime = email.message("ann.lee@contoso.com", "Summary", "Attached.", attachments={"summary.xlsx": frame, "summary.csv": frame})
    assert [(part.get_filename(), part.get_content_type()) for part in mime.iter_attachments()] == [
        ("summary.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("summary.csv", "text/csv"),
    ]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (Format(), "#,##0.00"),
        (Format("number", 0), "#,##0"),
        (Format("price"), "#,##0.00"),
        (Format("pct", 1), "0.0%"),
        (Format("bps", 0), "0.00%"),
        (Format("money", currency="JPY"), '"¥"#,##0;-"¥"#,##0'),
        (Format("money", currency="CHF"), f'"CHF{NBSP}"#,##0.00;-"CHF{NBSP}"#,##0.00'),
        (Format("price32"), "# ??/32"),
        (Format("price32", step="half"), "# ??/64"),
        (Format("price32", step="quarter"), "# ???/128"),
        (Format("price32", step="eighth"), "# ???/256"),
    ],
)
def test_excel_formats_follow_the_page(given: Format, expected: str) -> None:
    assert email.excel_format(given) == expected


@pytest.mark.parametrize(
    ("value", "given", "expected"),
    [
        (None, None, ""),
        (float("nan"), None, ""),
        (pd.NaT, None, ""),
        (True, Format("number", 0), "Yes"),
        (3, None, "3.00"),
        (0.0525, Format("pct"), "5.25%"),
        (pd.Timestamp("2026-11-13"), None, "2026-11-13"),
        (pd.Timestamp("2026-11-13 07:05"), None, "2026-11-13 07:05"),
        ("Gov", Format("pct"), "Gov"),
    ],
)
def test_a_cell_as_text(value: object, given: Format | None, expected: str) -> None:
    assert email.cell(value, given) == expected


class FakeSMTP:
    sent: list[tuple[str, int, EmailMessage]] = []  # noqa: RUF012 - the one record a test reads back

    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        del timeout
        self.host, self.port = host, port

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def send_message(self, mime: EmailMessage) -> None:
        FakeSMTP.sent.append((self.host, self.port, mime))


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, EmailMessage]]:
    FakeSMTP.sent = []
    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("CTZD_SMTP_HOST", "relay.contoso.com")
    monkeypatch.setenv("CTZD_SMTP_PORT", "2525")
    return FakeSMTP.sent


def test_locally_an_email_is_saved_to_the_outbox(tmp_path: Path, relay: list[tuple[str, int, EmailMessage]]) -> None:
    email.send("ann.lee@contoso.com", "Summary", "Today's summary.", table=summary())
    (saved,) = (tmp_path / ".ctzd" / "outbox").glob("email-*.eml")
    assert b"Subject: Summary" in saved.read_bytes()
    assert relay == []


def test_the_outbox_can_be_moved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_OUTBOX", str(tmp_path / "elsewhere"))
    email.send("ann.lee@contoso.com", "Summary", "Body.")
    assert len(list((tmp_path / "elsewhere").glob("email-*.eml"))) == 1


def test_in_dev_an_email_goes_only_to_the_owners_marked_dev(monkeypatch: pytest.MonkeyPatch, relay: list[tuple[str, int, EmailMessage]]) -> None:
    monkeypatch.setenv("CTZD_ENV", "dev")
    monkeypatch.setenv("CTZD_OWNERS", "owner.one@contoso.com,owner.two@contoso.com")
    monkeypatch.setenv("CTZD_SMTP_SENDER", "fi-bond-prices@contoso.com")
    email.send(["desk@contoso.com"], "Summary", "Today's summary.")
    ((host, port, mime),) = relay
    assert (host, port) == ("relay.contoso.com", 2525)
    assert mime["To"] == "owner.one@contoso.com, owner.two@contoso.com"
    assert mime["From"] == "fi-bond-prices@contoso.com"
    assert mime["Subject"] == "[dev] Summary"
    assert plain_of(mime).startswith("[dev] From the app's dev deployment. In prod this goes to: desk@contoso.com.\n\nToday's summary.")


def test_in_prod_an_email_goes_to_whoever_the_code_names(monkeypatch: pytest.MonkeyPatch, relay: list[tuple[str, int, EmailMessage]]) -> None:
    monkeypatch.setenv("CTZD_ENV", "prod")
    monkeypatch.setenv("CTZD_OWNERS", "owner.one@contoso.com")
    email.send("desk@contoso.com", "Summary", "Today's summary.")
    ((_, _, mime),) = relay
    assert (mime["To"], mime["Subject"]) == ("desk@contoso.com", "Summary")


def test_messages_off_drops_the_email(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relay: list[tuple[str, int, EmailMessage]]) -> None:
    monkeypatch.setenv("CTZD_MESSAGES", "off")
    for env in ("local", "dev", "prod"):
        monkeypatch.setenv("CTZD_ENV", env)
        email.send("desk@contoso.com", "Summary", "Body.")
    assert relay == []
    assert not (tmp_path / ".ctzd").exists()


def test_off_the_cluster_there_is_no_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_ENV", "prod")
    with pytest.raises(EmailError, match="CTZD_SMTP_HOST"):
        email.send("desk@contoso.com", "Summary", "Body.")


def test_display_and_email_agree() -> None:
    assert email.cell(2.675, Format()) == display.number(2.675) == "2.68"
    assert messages.DEV_MARK == "[dev]"
