"""Where a message goes, by environment, the one rule for email and Slack alike:

- **local** (a desktop, `ctzd run --local`, tests): saved under `.ctzd/outbox/` (`CTZD_OUTBOX`) and never sent;
- **dev**: sent only to the app's owners (`CTZD_OWNERS`), marked `[dev]`, so a job deployed to dev never writes to
  the desk a second time;
- **prod**: sent to whoever the code names.

`CTZD_MESSAGES=off` drops every message, as the smoke test's run of a job does.
"""

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

type Environment = Literal["local", "dev", "prod"]
DEV_MARK: Final = "[dev]"
_LOG = logging.getLogger("ctzd_common.messages")


@dataclass(frozen=True)
class Where:
    env: Environment
    owners: tuple[str, ...] = field(default=())
    outbox: Path = Path(".ctzd/outbox")
    off: bool = False


def where() -> Where:
    given = os.environ.get("CTZD_ENV", "local")
    env: Environment = "prod" if given == "prod" else "dev" if given == "dev" else "local"
    owners = tuple(owner for owner in os.environ.get("CTZD_OWNERS", "").split(",") if owner)
    return Where(env=env, owners=owners, outbox=Path(os.environ.get("CTZD_OUTBOX", ".ctzd/outbox")), off=os.environ.get("CTZD_MESSAGES", "") == "off")


class MessageError(RuntimeError):
    """A message that cannot go where the environment says it goes; the message says why."""


def owners(place: Where) -> tuple[str, ...]:
    """Who a message goes to in dev: the app's owners, which the platform gives every pod."""
    if not place.owners:
        msg = "In dev a message goes only to the app's owners, and none are set (CTZD_OWNERS): list them in app.toml's [app].owners."
        raise MessageError(msg)
    return place.owners


def save(kind: str, suffix: str, content: bytes, place: Where) -> Path:
    """Keep a message in the outbox instead of sending it; return where."""
    place.outbox.mkdir(parents=True, exist_ok=True)
    path = place.outbox / f"{kind}-{secrets.token_hex(4)}{suffix}"
    path.write_bytes(content)
    _LOG.warning("Saved the %s to %s instead of sending it: messages are sent only from dev and prod.", kind, path)
    return path
