"""A pytest plugin that runs every test with no network: `pytest -p ctzd_kit.pytest_nonetwork`."""

from collections.abc import Generator

import pytest

from ctzd_kit.nonetwork import blocked


@pytest.hookimpl(wrapper=True)
def pytest_runtestloop(session: pytest.Session) -> Generator[None, object, object]:
    del session
    with blocked():
        return (yield)
