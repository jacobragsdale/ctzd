from dataclasses import dataclass


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    mime_type: str


@dataclass(frozen=True)
class EmailMessage:
    to: tuple[str, ...]
    subject: str
    body: str
    attachments: tuple[Attachment, ...]
    # The same message for mail readers that show HTML: the table formatted as the page formats it.
    html: str | None = None


@dataclass(frozen=True)
class TeamsMessage:
    source: str
    title: str | None
    text: str


SentMessage = EmailMessage | TeamsMessage
