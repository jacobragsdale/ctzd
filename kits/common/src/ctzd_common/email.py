"""Email from a job or a page, through the platform's mail relay, with tables written as the page writes them.

    from ctzd_common import display, email

    summary = email.Table(frame, {"market_value": display.Format("money", 0, currency="USD")}, title="By sector")
    email.send(["ann.lee@contoso.com"], "Sector summary", "Today's sector summary.", table=summary,
               attachments={"sector_summary.xlsx": summary})

The message has a plain body and an HTML one, with the table formatted by its column formats; an `.xlsx` attachment
keeps every number raw, shown through Excel number formats from the same formats, with a frozen header and a filter.
Where it goes depends on the environment (`ctzd_common.messages`).
"""

import datetime as dt
import html
import io
import logging
import os
import re
import smtplib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage

import pandas as pd

from ctzd_common import messages
from ctzd_common.display import STEPS, Format, text

_LOG = logging.getLogger("ctzd_common.email")
_TIMEOUT_SECONDS = 30.0
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_WIDEST = 60


class EmailError(ValueError):
    """An email that cannot be sent as given; the message says what to change."""


@dataclass(frozen=True)
class Table:
    """A frame and the format of each of its number columns, as the page names them."""

    frame: pd.DataFrame
    formats: Mapping[str, Format] = field(default_factory=dict)
    title: str | None = None


type Attached = pd.DataFrame | Table


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    mime_type: str


def cell(value: object, given: Format | None) -> str:
    if value is None or value is pd.NaT or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int | float):
        return text(float(value), given)
    if isinstance(value, pd.Timestamp | dt.datetime):
        return value.strftime("%Y-%m-%d") if value.time() == dt.time(0) else value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def html_table(table: Table) -> str:
    columns = [str(column) for column in table.frame.columns]
    style = "padding: 4px 10px; border-bottom: 1px solid #ddd"

    def align(column: str) -> str:
        return "right" if column in table.formats or pd.api.types.is_numeric_dtype(table.frame[column]) else "left"

    head = "".join(f'<th style="{style}; text-align: {align(column)}">{html.escape(column)}</th>' for column in columns)
    rows = [
        "<tr>"
        + "".join(f'<td style="{style}; text-align: {align(column)}">{html.escape(cell(value, table.formats.get(column)))}</td>' for column, value in zip(columns, record, strict=True))
        + "</tr>"
        for record in table.frame.itertuples(index=False, name=None)
    ]
    caption = f'<caption style="text-align: left; font-weight: bold">{html.escape(table.title)}</caption>' if table.title is not None else ""
    return f'<table style="border-collapse: collapse">{caption}<thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def excel_format(given: Format) -> str:
    """The Excel number format that shows a raw float as the page shows it. Excel cannot multiply by 10,000, so basis
    points show as a percentage to two more decimals: 0.00125 is 0.1250%, which is 12.50 bp."""
    if given.kind == "price32":
        per_point = 32 * STEPS[given.step or "32nd"]
        return f"# {'?' * len(str(per_point))}/{per_point}"
    decimals = given.shown_decimals
    places = "." + "0" * decimals if decimals else ""
    if given.kind == "pct":
        return f"0{places}%"
    if given.kind == "bps":
        return f"0.{'0' * (decimals + 2)}%"
    if given.kind == "money":
        prefix = re.split(r"\d", text(1.0, given), maxsplit=1)[0]
        quoted = (f'"{prefix}"' if prefix else "") + f"#,##0{places}"
        return f"{quoted};-{quoted}"
    return f"#,##0{places}"


def _workbook(item: Attached) -> bytes:
    from openpyxl.utils import get_column_letter  # noqa: PLC0415 - openpyxl loads only for a workbook

    frame = item.frame if isinstance(item, Table) else item
    formats = item.formats if isinstance(item, Table) else {}
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Data")
        sheet = writer.sheets["Data"]
        sheet.freeze_panes = "A2"
        if len(frame.columns):
            sheet.auto_filter.ref = sheet.dimensions
        for position, column in enumerate(frame.columns, start=1):
            given = formats.get(str(column))
            shape = excel_format(given) if given is not None else "yyyy-mm-dd" if pd.api.types.is_datetime64_any_dtype(frame[column]) else None
            if shape is not None:
                for (written,) in sheet.iter_rows(min_row=2, min_col=position, max_col=position):
                    written.number_format = shape
            widths = [len(str(column)), *(len(cell(value, given)) for value in frame[column].head(200))]
            sheet.column_dimensions[get_column_letter(position)].width = min(max(widths) + 2, _WIDEST)
    return buffer.getvalue()


def attachment(filename: str, item: Attached) -> Attachment:
    frame = item.frame if isinstance(item, Table) else item
    if filename.endswith(".xlsx"):
        return Attachment(filename, _workbook(item), _XLSX)
    if filename.endswith(".csv"):
        return Attachment(filename, frame.to_csv(index=False).encode(), "text/csv")
    msg = f"Attachment {filename!r} must end in .xlsx or .csv."
    raise EmailError(msg)


def message(  # noqa: PLR0913 - the parts of one email, every one after `body` keyword-only
    to: str | Sequence[str], subject: str, body: str, *, table: Table | None = None, attachments: Mapping[str, Attached] | None = None, link: str | None = None, sender: str = "ctzd@contoso.com"
) -> EmailMessage:
    recipients = (to,) if isinstance(to, str) else tuple(to)
    if not recipients:
        msg = "An email needs at least one recipient."
        raise EmailError(msg)
    mime = EmailMessage()
    mime["From"] = sender
    mime["To"] = ", ".join(recipients)
    mime["Subject"] = subject
    mime.set_content("\n\n".join([body, *([f"See it live: {link}"] if link is not None else [])]))
    if table is not None or link is not None:
        paragraphs = [f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>" for part in body.split("\n\n") if part.strip()]
        paragraphs += [html_table(table)] if table is not None else []
        paragraphs += [f'<p><a href="{html.escape(link, quote=True)}">See it live</a></p>'] if link is not None else []
        mime.add_alternative('<!doctype html><html><body style="font-family: Segoe UI, Arial, sans-serif; font-size: 14px">' + "".join(paragraphs) + "</body></html>", subtype="html")
    for filename, item in (attachments or {}).items():
        found = attachment(filename, item)
        maintype, subtype = found.mime_type.split("/", 1)
        mime.add_attachment(found.content, maintype=maintype, subtype=subtype, filename=found.filename)
    return mime


def send(  # noqa: PLR0913 - the parts of one email, every one after `body` keyword-only
    to: str | Sequence[str], subject: str, body: str, *, table: Table | None = None, attachments: Mapping[str, Attached] | None = None, link: str | None = None
) -> None:
    """Send an email, or keep it, as the environment says (`ctzd_common.messages`)."""
    place = messages.where()
    if place.off:
        return
    if place.env == "dev":
        named = ", ".join((to,) if isinstance(to, str) else to)
        body = f"{messages.DEV_MARK} From the app's dev deployment. In prod this goes to: {named}.\n\n{body}"
        to, subject = messages.owners(place), f"{messages.DEV_MARK} {subject}"
    mime = message(to, subject, body, table=table, attachments=attachments, link=link, sender=os.environ.get("CTZD_SMTP_SENDER", "ctzd@contoso.com"))
    if place.env == "local":
        messages.save("email", ".eml", mime.as_bytes(), place)
        return
    host, port = os.environ.get("CTZD_SMTP_HOST", ""), os.environ.get("CTZD_SMTP_PORT", "25")
    if not host:
        msg = "Email needs the platform's mail relay (CTZD_SMTP_HOST), which only the cluster has."
        raise EmailError(msg)
    with smtplib.SMTP(host, int(port), timeout=_TIMEOUT_SECONDS) as smtp:
        smtp.send_message(mime)
    _LOG.info("emailed %r to %s", subject, mime["To"])
