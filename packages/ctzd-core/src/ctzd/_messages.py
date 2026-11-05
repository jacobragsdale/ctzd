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


@dataclass(frozen=True)
class TeamsMessage:
    source: str
    title: str | None
    text: str


SentMessage = EmailMessage | TeamsMessage
