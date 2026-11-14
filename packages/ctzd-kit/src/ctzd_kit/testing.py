"""For a kit's own tests: its sources faked, the store in a folder, and no network.

with ctzd_kit.testing.fakes():
    assert not fi_pricing.prices(date(2026, 11, 13)).empty   # the source's sample rows

with ctzd_kit.testing.store(tmp_path):
    ...                                                    # write_table and read_table under tmp_path
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ctzd_common import samples
from ctzd_kit import cache, nonetwork
from ctzd_kit import store as tables
from ctzd_kit.settings import _override


@contextmanager
def given(variables: Mapping[str, str]) -> Generator[None]:
    """The settings `variables` give, `CTZD_ENV` and the rest, in place of the environment's, for the block."""
    previous = _override.get() or {}
    token = _override.set({**previous, **variables})
    cache.clear_all()
    try:
        yield
    finally:
        _override.reset(token)
        cache.clear_all()


@contextmanager
def fakes(fixtures: Path | None = None) -> Generator[None]:
    """Every source serves its sample rows for the block, the app's own `fixtures` first where it is given."""
    samples.reset()
    with given({"CTZD_FAKE": "1", "CTZD_FIXTURES": "" if fixtures is None else str(fixtures)}):
        yield


@contextmanager
def store(root: Path, *, sbu: str = "test", app: str = "test") -> Generator[Path]:
    with tables.local(root, sbu=sbu, app=app) as found:
        yield found


blocked = nonetwork.blocked
