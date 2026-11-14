"""Which sources served sample rows, in the process and in a watched block."""

import os
from pathlib import Path

import pytest

from ctzd_common import samples


@pytest.fixture(autouse=True)
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop, in the test's own folder, with no message settings from the environment."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    samples.reset()


def test_marks_are_kept_for_the_process_until_reset() -> None:
    samples.mark("fi_pricing")
    samples.mark("fi_pricing")
    samples.mark("sample")
    assert samples.served() == {"fi_pricing", "sample"}
    samples.reset()
    assert samples.served() == frozenset()


def test_watching_sees_only_the_marks_inside_its_block() -> None:
    samples.mark("before")
    with samples.watching() as outer:
        samples.mark("fi_pricing")
        with samples.watching() as inner:
            samples.mark("fi_risk")
        samples.mark("fi_curves")
    samples.mark("after")
    assert outer == {"fi_pricing", "fi_risk", "fi_curves"}, "a block inside the watched one is inside it too"
    assert inner == {"fi_risk"}
    assert samples.served() == {"before", "fi_pricing", "fi_risk", "fi_curves", "after"}
