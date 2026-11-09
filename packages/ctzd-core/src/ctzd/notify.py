"""`ctzd.notify`: email and Microsoft Teams messages from jobs.

In the cluster, email goes through the SMTP relay and Teams messages through a
Workflows webhook stored as a `teams` source. Locally (`CTZD_ENV=local`) both
are written to `.ctzd/out/outbox/` instead of being sent.

A table goes into an email as a `Table`, a frame with the row type that says how
each column shows, as a dataset's does:

    summary = ctzd.notify.Table(frame, SectorSummary)
    ctzd.notify.email(to, "Sector summary", "Today's sector summary.", table=summary,
                      attachments={"sector_summary.xlsx": summary}, as_of=day, link="https://fi-bond-prices.ctzd.contoso.com")

The email then has an HTML body beside the plain one, with the table formatted
as the page formats it, the date the data is for, and the link; and the
workbook keeps every number raw, shown through Excel number formats from the
column kinds, with a frozen header, column widths, and a filter.
"""

import datetime as dt
import html
import io
import json
import logging
import os
import re
import secrets
import smtplib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from pathlib import Path

import httpx
import pandas as pd
from basis.api.format import Formatted
from basis.core.display import text
from openpyxl.utils import get_column_letter

from ctzd import _overrides
from ctzd._errors import CtzdError, SourceError
from ctzd._messages import Attachment, EmailMessage, SentMessage, TeamsMessage
from ctzd.datasets import Row
from ctzd.settings import app_settings, mail_settings
from ctzd.sources import TeamsSource, check_name, load_source

_LOG = logging.getLogger("ctzd.notify")
_TIMEOUT_SECONDS = 30.0
OUTBOX = Path(".ctzd/out/outbox")
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class Table:
    """A frame and its row type (a `ctzd.Row`, as a dataset declares one), so an email shows each column as the page does."""

    frame: pd.DataFrame
    row: type[Row]
    title: str | None = None

    def formats(self) -> dict[str, dict[str, object]]:
        """Each column's display format, from the row type's column kinds."""
        found: dict[str, dict[str, object]] = {}
        for name, field in self.row.model_fields.items():
            formatted = next((item for item in field.metadata if isinstance(item, Formatted)), None)
            if formatted is not None:
                found[name] = formatted.display.as_json()
        return found


Attached = pd.DataFrame | Table
_DECIMALS = re.compile(r"\.(\d+)")
_WIDEST = 60


def email_message(  # noqa: PLR0913 - the parts of one email, every one after `body` keyword-only
    to: str | Sequence[str], subject: str, body: str, *, attachments: Mapping[str, Attached] | None = None, table: Table | None = None, as_of: dt.date | None = None, link: str | None = None
) -> EmailMessage:
    recipients = (to,) if isinstance(to, str) else tuple(to)
    if not recipients:
        msg = "email needs at least one recipient."
        raise CtzdError(msg)
    files = tuple(_attachment(filename, item) for filename, item in (attachments or {}).items())
    lines = [body, *([f"Data for {_date(as_of)}."] if as_of is not None else []), *([f"See it live: {link}"] if link is not None else [])]
    rich = None if table is None and as_of is None and link is None else _html(body, table, as_of, link)
    return EmailMessage(to=recipients, subject=subject, body="\n\n".join(lines), attachments=files, html=rich)


def email(  # noqa: PLR0913 - the parts of one email, every one after `body` keyword-only
    to: str | Sequence[str], subject: str, body: str, *, attachments: Mapping[str, Attached] | None = None, table: Table | None = None, as_of: dt.date | None = None, link: str | None = None
) -> None:
    """Send an email. `attachments` maps a file name ending in .xlsx or .csv to a frame or a `Table`; `table` shows a
    `Table` in the message itself, `as_of` says which day the data is for, and `link` points at the dashboard."""
    _deliver(email_message(to, subject, body, attachments=attachments, table=table, as_of=as_of, link=link))


def teams(source: str, text: str, *, title: str | None = None) -> None:
    """Post `text` to the Teams channel behind the `teams` source `source`."""
    _deliver(TeamsMessage(source=check_name(source), title=title, text=text))


def _date(day: dt.date) -> str:
    return f"{day.day} {day:%B %Y}"


def _cell(value: object, display: Mapping[str, object] | None) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return ""
    if display is not None and isinstance(value, int | float):
        return text(float(value), dict(display))
    if isinstance(value, pd.Timestamp | dt.datetime):
        return value.strftime("%Y-%m-%d") if value.time() == dt.time(0) else value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def _html(body: str, table: Table | None, as_of: dt.date | None, link: str | None) -> str:
    paragraphs = [f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>" for part in body.split("\n\n") if part.strip()]
    if as_of is not None:
        paragraphs.append(f"<p>Data for {html.escape(_date(as_of))}.</p>")
    if table is not None:
        paragraphs.append(_html_table(table))
    if link is not None:
        paragraphs.append(f'<p><a href="{html.escape(link, quote=True)}">See it live</a></p>')
    return '<!doctype html><html><body style="font-family: Segoe UI, Arial, sans-serif; font-size: 14px">' + "".join(paragraphs) + "</body></html>"


def _html_table(table: Table) -> str:
    formats = table.formats()
    columns = [str(column) for column in table.frame.columns]
    cell = "padding: 4px 10px; border-bottom: 1px solid #ddd"
    head = "".join(f'<th style="{cell}; text-align: {"right" if column in formats else "left"}">{html.escape(column)}</th>' for column in columns)
    rows = []
    for record in table.frame.itertuples(index=False, name=None):
        cells = "".join(
            f'<td style="{cell}; text-align: {"right" if column in formats else "left"}">{html.escape(_cell(value, formats.get(column)))}</td>' for column, value in zip(columns, record, strict=True)
        )
        rows.append(f"<tr>{cells}</tr>")
    caption = f'<caption style="text-align: left; font-weight: bold">{html.escape(table.title)}</caption>' if table.title is not None else ""
    return f'<table style="border-collapse: collapse">{caption}<thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def excel_format(display: Mapping[str, object]) -> str:
    """The Excel number format that shows a raw float as the page shows it. Excel cannot multiply by 10,000, so basis
    points show as a percentage to two more decimals: 0.00125 is 0.1250%, which is 12.50 bp."""
    kind = display.get("kind")
    if kind == "price32":
        return "# ??/64" if display.get("step") not in {None, "32nd"} else "# ??/32"
    sample = text(1.0, dict(display))
    found = _DECIMALS.search(sample)
    decimals = len(found.group(1)) if found is not None else 0
    places = "." + "0" * decimals if decimals else ""
    if kind == "pct":
        return f"0{places}%"
    if kind == "bps":
        return f"0.{'0' * (decimals + 2)}%"
    if kind == "money":
        prefix, _number, suffix = re.split(r"(\d[\d,.]*)", text(1.0, dict(display)), maxsplit=1)
        quoted = "".join(f'"{part}"' for part in (prefix,) if part) + f"#,##0{places}" + "".join(f'"{part}"' for part in (suffix,) if part)
        return f"{quoted};-{quoted}"
    return f"#,##0{places}"


def _workbook(item: Attached) -> bytes:
    frame = item.frame if isinstance(item, Table) else item
    formats = item.formats() if isinstance(item, Table) else {}
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Data")
        sheet = writer.sheets["Data"]
        sheet.freeze_panes = "A2"
        if len(frame.columns):
            sheet.auto_filter.ref = sheet.dimensions
        for position, column in enumerate(frame.columns, start=1):
            letter = get_column_letter(position)
            display = formats.get(str(column))
            number_format = excel_format(display) if display is not None else "yyyy-mm-dd" if pd.api.types.is_datetime64_any_dtype(frame[column]) else None
            if number_format is not None:
                for (cell,) in sheet.iter_rows(min_row=2, min_col=position, max_col=position):
                    cell.number_format = number_format
            shown = [len(str(column)), *(len(_cell(value, display)) for value in frame[column].head(200))]
            sheet.column_dimensions[letter].width = min(max(shown) + 2, _WIDEST)
    return buffer.getvalue()


def _attachment(filename: str, item: Attached) -> Attachment:
    frame = item.frame if isinstance(item, Table) else item
    if filename.endswith(".xlsx"):
        return Attachment(filename=filename, content=_workbook(item), mime_type=_XLSX)
    if filename.endswith(".csv"):
        return Attachment(filename=filename, content=frame.to_csv(index=False).encode(), mime_type="text/csv")
    msg = f"Attachment {filename!r} must end in .xlsx or .csv."
    raise CtzdError(msg)


def teams_card(message: TeamsMessage) -> dict[str, object]:
    body: list[dict[str, object]] = []
    if message.title is not None:
        body.append({"type": "TextBlock", "text": message.title, "weight": "Bolder", "size": "Medium", "wrap": True})
    body.append({"type": "TextBlock", "text": message.text, "wrap": True})
    card = {"type": "AdaptiveCard", "version": "1.4", "body": body}
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def _deliver(message: SentMessage) -> None:
    outbox = _overrides.current().outbox
    if outbox is not None:
        outbox.append(message)
        return
    if app_settings().env == "local":
        _save_locally(message)
        return
    match message:
        case EmailMessage():
            _send_email(message)
        case TeamsMessage():
            _post_teams(message)


def _mime(message: EmailMessage, sender: str) -> MimeMessage:
    mime = MimeMessage()
    mime["From"] = sender
    mime["To"] = ", ".join(message.to)
    mime["Subject"] = message.subject
    mime.set_content(message.body)
    if message.html is not None:
        mime.add_alternative(message.html, subtype="html")
    for item in message.attachments:
        maintype, subtype = item.mime_type.split("/", 1)
        mime.add_attachment(item.content, maintype=maintype, subtype=subtype, filename=item.filename)
    return mime


def _send_email(message: EmailMessage) -> None:
    settings = mail_settings()
    with smtplib.SMTP(settings.host, settings.port, timeout=_TIMEOUT_SECONDS) as smtp:
        smtp.send_message(_mime(message, settings.sender))
    _LOG.info("emailed %r to %s", message.subject, ", ".join(message.to))


def _post_teams(message: TeamsMessage) -> None:
    config = load_source(message.source, os.environ)
    if not isinstance(config, TeamsSource):
        msg = f"Source {message.source!r} is a {config.kind} source, not a teams webhook."
        raise SourceError(msg)
    response = httpx.post(config.webhook_url.get_secret_value(), json=teams_card(message), timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    _LOG.info("posted to Teams source %s", message.source)


def _save_locally(message: SentMessage) -> None:
    OUTBOX.mkdir(parents=True, exist_ok=True)
    match message:
        case EmailMessage():
            path = OUTBOX / f"email-{secrets.token_hex(4)}.eml"
            path.write_bytes(_mime(message, "ctzd-local@localhost").as_bytes())
        case TeamsMessage():
            path = OUTBOX / f"teams-{secrets.token_hex(4)}.json"
            path.write_text(json.dumps(teams_card(message), indent=2), encoding="utf-8")
    _LOG.warning("CTZD_ENV=local: saved the message to %s instead of sending it", path)
