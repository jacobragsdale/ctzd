import datetime as dt

import pandas as pd
import pytest

import ctzd
from ctzd.cache import parse_ttl


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(("ttl", "seconds"), [("30s", 30.0), ("15m", 900.0), ("2h", 7200.0), ("1d", 86400.0), (45, 45.0), (dt.timedelta(minutes=2), 120.0)])
def test_parse_ttl(ttl: str | int | dt.timedelta, seconds: float) -> None:
    assert parse_ttl(ttl) == seconds


@pytest.mark.parametrize("ttl", ["15", "m", "1w", 0, "0s"])
def test_parse_ttl_rejects_unusable_values(ttl: str | int) -> None:
    with pytest.raises(ctzd.CtzdError):
        parse_ttl(ttl)


def test_result_is_reused_until_the_ttl_expires() -> None:
    clock = FakeClock()
    calls: list[str] = []

    @ctzd.cached(ttl="1m", clock=clock)
    def load(day: str) -> str:
        calls.append(day)
        return day.upper()

    assert load("mon") == "MON"
    assert load("mon") == "MON"
    clock.now += 59
    load("mon")
    assert calls == ["mon"]
    clock.now += 2
    load("mon")
    assert calls == ["mon", "mon"]


def test_different_arguments_are_cached_separately() -> None:
    calls: list[str] = []

    @ctzd.cached(ttl="1h", clock=FakeClock())
    def load(day: str) -> str:
        calls.append(day)
        return day

    load("mon")
    load("tue")
    load("mon")
    assert calls == ["mon", "tue"]


def test_oldest_entry_is_evicted_beyond_max_entries() -> None:
    calls: list[int] = []

    @ctzd.cached(ttl="1h", max_entries=2, clock=FakeClock())
    def load(n: int) -> int:
        calls.append(n)
        return n

    for n in (1, 2, 3, 1):
        load(n)
    assert calls == [1, 2, 3, 1]


def test_changing_a_returned_frame_does_not_change_the_cache() -> None:
    @ctzd.cached(ttl="1h", clock=FakeClock())
    def load() -> pd.DataFrame:
        return pd.DataFrame({"price": [1.0, 2.0]})

    first = load()
    first.loc[0, "price"] = 99.0
    assert load()["price"].tolist() == [1.0, 2.0]


def test_unhashable_arguments_explain_the_rule() -> None:
    @ctzd.cached(ttl="1h")
    def load(days: list[str]) -> int:
        return len(days)

    with pytest.raises(ctzd.CtzdError, match="hashable"):
        load(["mon"])
