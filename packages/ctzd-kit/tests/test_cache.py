"""`@cached`: a loader's result is reused per arguments until its time is up."""

import datetime as dt

import pandas as pd
import pytest

from ctzd_kit import KitError, cached, testing
from ctzd_kit.cache import clear_all, parse_ttl


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(("ttl", "seconds"), [("30s", 30.0), ("15m", 900.0), (" 2h ", 7200.0), ("1d", 86400.0), (45, 45.0), (dt.timedelta(minutes=2), 120.0)])
def test_parse_ttl(ttl: str | int | dt.timedelta, seconds: float) -> None:
    assert parse_ttl(ttl) == seconds


@pytest.mark.parametrize("ttl", ["15", "m", "1w", 0, "0s", -5, dt.timedelta(0)])
def test_parse_ttl_refuses_what_cannot_be_a_duration(ttl: str | int | dt.timedelta) -> None:
    with pytest.raises(KitError):
        parse_ttl(ttl)


def test_a_result_is_reused_until_the_ttl_is_up() -> None:
    clock = FakeClock()
    calls: list[str] = []

    @cached(ttl="1m", clock=clock)
    def load(day: str) -> str:
        calls.append(day)
        return day.upper()

    assert load("mon") == "MON"
    clock.now += 59
    assert load("mon") == "MON"
    assert calls == ["mon"]
    clock.now += 2
    load("mon")
    assert calls == ["mon", "mon"]


def test_each_set_of_arguments_is_cached_on_its_own() -> None:
    calls: list[tuple[str, int]] = []

    @cached(ttl="1h", clock=FakeClock())
    def load(day: str, *, page: int = 1) -> str:
        calls.append((day, page))
        return day

    load("mon")
    load("tue")
    load("mon", page=2)
    load("mon")
    load("mon", page=2)
    assert calls == [("mon", 1), ("tue", 1), ("mon", 2)]


def test_the_least_recently_used_entry_goes_beyond_max_entries() -> None:
    calls: list[int] = []

    @cached(ttl="1h", max_entries=2, clock=FakeClock())
    def load(n: int) -> int:
        calls.append(n)
        return n

    for n in (1, 2, 1, 3, 1, 2):
        load(n)
    assert calls == [1, 2, 3, 2]


def test_changing_a_returned_frame_does_not_change_the_cache() -> None:
    @cached(ttl="1h", clock=FakeClock())
    def load() -> pd.DataFrame:
        return pd.DataFrame({"price": [1.0, 2.0]})

    first = load()
    first.loc[0, "price"] = 99.0
    first["extra"] = 1.0
    assert load().to_dict("list") == {"price": [1.0, 2.0]}


def test_unhashable_arguments_explain_the_rule() -> None:
    @cached(ttl="1h")
    def load(days: list[str]) -> int:
        return len(days)

    with pytest.raises(KitError, match="hashable"):
        load(["mon"])


def test_clearing_every_cache_and_the_fakes_start_afresh() -> None:
    calls: list[int] = []

    @cached(ttl="1h", clock=FakeClock())
    def load() -> int:
        calls.append(1)
        return len(calls)

    assert load() == 1
    clear_all()
    assert load() == 2
    with testing.fakes():
        assert load() == 3
    assert load() == 4
