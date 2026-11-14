"""Where a message goes: the one rule for email and Slack alike."""

import os
from pathlib import Path

import pytest

from ctzd_common import messages, samples


@pytest.fixture(autouse=True)
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop, in the test's own folder, with no message settings from the environment."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    samples.reset()


def test_by_default_it_is_the_desktop() -> None:
    assert messages.where() == messages.Where(env="local", owners=(), outbox=Path(".ctzd/outbox"), off=False)


@pytest.mark.parametrize(("given", "env"), [("local", "local"), ("dev", "dev"), ("prod", "prod"), ("staging", "local"), ("", "local")])
def test_the_environment_is_local_dev_or_prod(given: str, env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_ENV", given)
    assert messages.where().env == env


def test_owners_outbox_and_off_come_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_OWNERS", "owner.one@contoso.com,,owner.two@contoso.com")
    monkeypatch.setenv("CTZD_OUTBOX", str(tmp_path / "box"))
    monkeypatch.setenv("CTZD_MESSAGES", "off")
    found = messages.where()
    assert found.owners == ("owner.one@contoso.com", "owner.two@contoso.com")
    assert found.outbox == tmp_path / "box"
    assert found.off


def test_only_off_turns_messages_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_MESSAGES", "on")
    assert not messages.where().off


def test_save_keeps_each_message_in_its_own_file(tmp_path: Path) -> None:
    place = messages.Where(env="local", outbox=tmp_path / "outbox")
    first = messages.save("email", ".eml", b"one", place)
    second = messages.save("email", ".eml", b"two", place)
    assert first != second
    assert first.parent == tmp_path / "outbox"
    assert first.name.startswith("email-")
    assert first.suffix == ".eml"
    assert (first.read_bytes(), second.read_bytes()) == (b"one", b"two")
