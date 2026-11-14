"""Slack from a job or a page: a message to a channel by its name, through the platform's one bot.

    from ctzd_common import display, email, slack

    slack.post("fi-desk", "Sector summary for 13 November 2026", table=email.Table(frame, formats))

A table is written into the message as a block of fixed-width text, each number as the page formats it. Where it goes
depends on the environment (`ctzd_common.messages`): in dev, each owner gets it as a direct message instead.
"""

import json
import logging
import os
from typing import Final

import httpx

from ctzd_common import messages
from ctzd_common.email import Table, cell

API: Final = "https://slack.com/api"
_TIMEOUT_SECONDS = 30.0
_WIDEST = 40
_LOG = logging.getLogger("ctzd_common.slack")


class SlackError(RuntimeError):
    """Slack refused the message; the message says why."""


def table_text(table: Table) -> str:
    """The table as fixed-width text, numbers right-aligned in the page's formats."""
    columns = [str(column) for column in table.frame.columns]
    cells = [[cell(value, table.formats.get(column))[:_WIDEST] for column, value in zip(columns, record, strict=True)] for record in table.frame.itertuples(index=False, name=None)]
    widths = [max([len(column), *(len(row[index]) for row in cells)]) for index, column in enumerate(columns)]
    numeric = [column in table.formats for column in columns]

    def line(values: list[str]) -> str:
        return "  ".join(value.rjust(width) if right else value.ljust(width) for value, width, right in zip(values, widths, numeric, strict=True)).rstrip()

    return "\n".join([*([] if table.title is None else [table.title]), line(columns), *(line(row) for row in cells)])


def payload(channel: str, text: str, table: Table | None) -> dict[str, object]:
    body = text if table is None else f"{text}\n```\n{table_text(table)}\n```"
    return {"channel": channel, "text": body, "unfurl_links": False}


def _call(client: httpx.Client, method: str, body: dict[str, object]) -> dict[str, object]:
    response = client.post(f"{API}/{method}", json=body)
    response.raise_for_status()
    answer: object = response.json()
    if not isinstance(answer, dict) or answer.get("ok") is not True:
        error = answer.get("error") if isinstance(answer, dict) else answer
        msg = f"Slack refused {method}: {error}. Check the channel's name, and that the platform's bot is in it."
        raise SlackError(msg)
    return {str(key): value for key, value in answer.items()}


def post(channel: str, text: str, *, table: Table | None = None) -> None:
    """Post `text`, and `table` beneath it, to the channel named `channel`, or keep it, as the environment says."""
    place = messages.where()
    if place.off:
        return
    name = channel.removeprefix("#")
    if place.env == "local":
        messages.save("slack", ".json", json.dumps(payload(f"#{name}", text, table), indent=2).encode(), place)
        return
    token = os.environ.get("CTZD_SLACK_TOKEN", "")
    if not token:
        msg = "Slack needs the platform's bot token (CTZD_SLACK_TOKEN), which only the cluster has."
        raise SlackError(msg)
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT_SECONDS) as client:
        if place.env == "prod":
            _call(client, "chat.postMessage", payload(f"#{name}", text, table))
            _LOG.info("posted to #%s", name)
            return
        for owner in messages.owners(place):
            user = _call(client, "users.lookupByEmail", {"email": owner}).get("user")
            member = user.get("id") if isinstance(user, dict) else None
            if isinstance(member, str):
                _call(client, "chat.postMessage", payload(member, f"{messages.DEV_MARK} For #{name} in prod:\n{text}", table))
        _LOG.info("posted the message for #%s to the owners, from dev", name)
