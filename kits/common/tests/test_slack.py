"""A Slack message's table text, and where the message goes in each environment."""

import functools
import json
import os
import re
from pathlib import Path

import httpx
import pandas as pd
import pytest

from ctzd_common import samples, slack
from ctzd_common.display import Format
from ctzd_common.email import Table
from ctzd_common.slack import SlackError

TABLE = Table(
    pd.DataFrame({"sector": ["Gov", "Corporate"], "par": [1234.5, -50.0], "bonds": [3.0, 12.0]}), {"par": Format("money", 0, currency="USD"), "bonds": Format("number", 0)}, title="By sector"
)


@pytest.fixture(autouse=True)
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop, in the test's own folder, with no message settings from the environment."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    samples.reset()


def test_a_table_is_fixed_width_text_with_numbers_right_aligned() -> None:
    assert slack.table_text(TABLE).splitlines() == ["By sector", "sector        par  bonds", "Gov        $1,235      3", "Corporate    -$50     12"]


def test_a_long_cell_is_cut_and_a_table_without_a_title_starts_with_its_header() -> None:
    text = slack.table_text(Table(pd.DataFrame({"note": ["x" * 50]})))
    assert text.splitlines() == ["note", "x" * 40]


def test_the_payload_puts_the_table_in_a_code_block() -> None:
    body = slack.payload("#fi-desk", "Sector summary", TABLE)
    assert body == {"channel": "#fi-desk", "text": f"Sector summary\n```\n{slack.table_text(TABLE)}\n```", "unfurl_links": False}
    assert slack.payload("#fi-desk", "Done", None)["text"] == "Done"


def test_locally_a_message_is_saved_to_the_outbox(tmp_path: Path) -> None:
    slack.post("#fi-desk", "Sector summary", table=TABLE)
    (saved,) = (tmp_path / ".ctzd" / "outbox").glob("slack-*.json")
    assert json.loads(saved.read_text(encoding="utf-8")) == slack.payload("#fi-desk", "Sector summary", TABLE)


class FakeSlack:
    """Slack's Web API as far as `post` uses it, answering each owner by email."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.tokens: set[str] = set()
        self.refuse = refuse

    def __call__(self, request: httpx.Request) -> httpx.Response:
        loaded: object = json.loads(request.content)
        assert isinstance(loaded, dict)
        body = {str(key): value for key, value in loaded.items()}
        method = request.url.path.removeprefix("/api/")
        self.calls.append((method, body))
        self.tokens.add(request.headers["Authorization"])
        if method == self.refuse:
            return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
        if method == "users.lookupByEmail":
            email = str(body["email"])
            return httpx.Response(200, json={"ok": True, "user": {"id": "U-" + email.partition("@")[0]}} if email != "gone@contoso.com" else {"ok": True})
        return httpx.Response(200, json={"ok": True})


def use(fake: FakeSlack, monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setattr(slack.httpx, "Client", functools.partial(httpx.Client, transport=httpx.MockTransport(fake)))
    monkeypatch.setenv("CTZD_SLACK_TOKEN", "xoxb-test")
    monkeypatch.setenv("CTZD_ENV", env)


def test_in_prod_a_message_goes_to_the_channel_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSlack()
    use(fake, monkeypatch, "prod")
    slack.post("fi-desk", "Sector summary", table=TABLE)
    assert fake.calls == [("chat.postMessage", slack.payload("#fi-desk", "Sector summary", TABLE))]
    assert fake.tokens == {"Bearer xoxb-test"}


def test_in_dev_each_owner_gets_it_directly_marked_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSlack()
    use(fake, monkeypatch, "dev")
    monkeypatch.setenv("CTZD_OWNERS", "owner.one@contoso.com,gone@contoso.com,owner.two@contoso.com")
    slack.post("#fi-desk", "Sector summary")
    assert fake.calls == [
        ("users.lookupByEmail", {"email": "owner.one@contoso.com"}),
        ("chat.postMessage", {"channel": "U-owner.one", "text": "[dev] For #fi-desk in prod:\nSector summary", "unfurl_links": False}),
        ("users.lookupByEmail", {"email": "gone@contoso.com"}),
        ("users.lookupByEmail", {"email": "owner.two@contoso.com"}),
        ("chat.postMessage", {"channel": "U-owner.two", "text": "[dev] For #fi-desk in prod:\nSector summary", "unfurl_links": False}),
    ]


def test_a_refusal_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    use(FakeSlack(refuse="chat.postMessage"), monkeypatch, "prod")
    with pytest.raises(SlackError, match=re.escape("refused chat.postMessage: channel_not_found")):
        slack.post("#fi-dsek", "Sector summary")


def test_an_http_error_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack.httpx, "Client", functools.partial(httpx.Client, transport=httpx.MockTransport(lambda _: httpx.Response(503))))
    monkeypatch.setenv("CTZD_SLACK_TOKEN", "xoxb-test")
    monkeypatch.setenv("CTZD_ENV", "prod")
    with pytest.raises(httpx.HTTPStatusError):
        slack.post("#fi-desk", "Sector summary")


def test_off_the_cluster_there_is_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_ENV", "dev")
    with pytest.raises(SlackError, match="CTZD_SLACK_TOKEN"):
        slack.post("#fi-desk", "Sector summary")


def test_messages_off_drops_the_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSlack()
    use(fake, monkeypatch, "prod")
    monkeypatch.setenv("CTZD_MESSAGES", "off")
    slack.post("#fi-desk", "Sector summary")
    monkeypatch.setenv("CTZD_ENV", "local")
    slack.post("#fi-desk", "Sector summary")
    assert fake.calls == []
    assert not (tmp_path / ".ctzd").exists()
