"""`ctzd.notify`: email and Microsoft Teams messages from jobs.

In the cluster, email goes through the SMTP relay and Teams messages through a
Workflows webhook stored as a `teams` source. Locally (`CTZD_ENV=local`) both
are written to `.ctzd/out/outbox/` instead of being sent.
"""

import io
import json
import logging
import os
import secrets
import smtplib
from collections.abc import Mapping, Sequence
from email.message import EmailMessage as MimeMessage
from pathlib import Path

import httpx
import pandas as pd

from ctzd import _overrides
from ctzd._errors import CtzdError, SourceError
from ctzd._messages import Attachment, EmailMessage, SentMessage, TeamsMessage
from ctzd.settings import app_settings, mail_settings
from ctzd.sources import TeamsSource, check_name, load_source

_LOG = logging.getLogger("ctzd.notify")
_TIMEOUT_SECONDS = 30.0
OUTBOX = Path(".ctzd/out/outbox")
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def email_message(to: str | Sequence[str], subject: str, body: str, *, attachments: Mapping[str, pd.DataFrame] | None = None) -> EmailMessage:
    recipients = (to,) if isinstance(to, str) else tuple(to)
    if not recipients:
        msg = "email needs at least one recipient."
        raise CtzdError(msg)
    files = tuple(_attachment(filename, frame) for filename, frame in (attachments or {}).items())
    return EmailMessage(to=recipients, subject=subject, body=body, attachments=files)


def email(to: str | Sequence[str], subject: str, body: str, *, attachments: Mapping[str, pd.DataFrame] | None = None) -> None:
    """Send an email. `attachments` maps a file name ending in .xlsx or .csv to the frame to attach."""
    _deliver(email_message(to, subject, body, attachments=attachments))


def teams(source: str, text: str, *, title: str | None = None) -> None:
    """Post `text` to the Teams channel behind the `teams` source `source`."""
    _deliver(TeamsMessage(source=check_name(source), title=title, text=text))


def _attachment(filename: str, frame: pd.DataFrame) -> Attachment:
    if filename.endswith(".xlsx"):
        buffer = io.BytesIO()
        frame.to_excel(buffer, index=False)
        return Attachment(filename=filename, content=buffer.getvalue(), mime_type=_XLSX)
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
