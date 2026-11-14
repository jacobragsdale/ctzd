"""Where the code runs, read from the environment the platform (or `ctzd dev`, `ctzd run --local`, `ctzd exec`) gives
it. Outside an app, as in a kit's own tests, it is the desktop with nothing faked."""

import os
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

type Environment = Literal["local", "dev", "prod"]
_ENVIRONMENTS: tuple[Environment, ...] = ("local", "dev", "prod")


@dataclass(frozen=True)
class Settings:
    env: Environment
    app: str
    sbu: str
    fake: bool
    """Every source serves its sample rows: `ctzd dev --fake`, `ctzd check`, tests."""
    fixtures: Path | None
    """The app's own sample rows, `fixtures/`, which override a kit's."""
    store_url: str | None
    owners: tuple[str, ...]


_override: ContextVar[Mapping[str, str] | None] = ContextVar("ctzd_kit_settings", default=None)
"""Variables a test gives in place of the environment's (`ctzd_kit.testing`)."""


def _get(name: str, default: str = "") -> str:
    given = _override.get()
    if given is not None and name in given:
        return given[name]
    return os.environ.get(name, default)


def settings() -> Settings:
    given = _get("CTZD_ENV", "local")
    env: Environment = next((name for name in _ENVIRONMENTS if name == given), "local")
    fixtures = _get("CTZD_FIXTURES")
    return Settings(
        env=env,
        app=_get("CTZD_APP"),
        sbu=_get("CTZD_SBU"),
        fake=_get("CTZD_FAKE") == "1",
        fixtures=Path(fixtures) if fixtures else None,
        store_url=_get("CTZD_STORE_URL") or None,
        owners=tuple(owner for owner in _get("CTZD_OWNERS").split(",") if owner),
    )


def session_name() -> str:
    """How a database session names the app (`ctzd:fi-bond-prices`), so a DBA can tell whose query it is."""
    found = settings()
    return f"ctzd:{found.sbu}-{found.app}" if found.app else "ctzd"
