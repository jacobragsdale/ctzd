---
name: ctzd-testing
description: "What to test in a ctzd app and how: table-driven logic tests, tiny frames built in code, ctzd.testing fakes for sources, the store, and messages, page and job tests, and the 70% branch-coverage gate. Use when writing, fixing, or reviewing tests, or when the tests step of ctzd check fails."
---

# Testing a ctzd app

## The gate

`ctzd check`, and so every deploy, runs pytest and fails unless the tests cover
at least 70% of the branches in `src/app/logic/` (set in the platform-owned
`pytest.toml`). Every warning is an error, so a pandas `ChainedAssignmentError`
or a deprecation fails the test. The 70% is a floor, not the goal: each test
must assert behavior a person would notice if it broke. Never write a test that
only runs code, weaken an assertion until it passes, or skip or xfail a failing
test. Fix the code or the test.

## What to test, in order

1. **Logic.** Every function in `src/app/logic/`, table-driven with
   `pytest.mark.parametrize`: one normal case, each rule's boundary (at the
   limit and just past it), and the empty frame. Assert on the returned value.
2. **Invariants.** A duplicated key makes each merge raise
   `pd.errors.MergeError`; a job rerun publishes the same table; a job with
   nothing to publish returns nonzero and sends nothing.
3. **Loaders.** Against fake sources: the loader returns the columns logic
   needs; a missing column raises `ctzd.DataError`.
4. **Each dataset.** Through the app, against fake sources: it answers the
   rows the page needs (see `ctzd-data`). The pages themselves are checked by
   `ctzd check`'s `web check` and `web build`, not by Python tests.
5. **Each job.** `run(day)` returns 0, writes its table, sends its messages
   (see `ctzd-jobs`).

Do not test React, parallax, pandas, or ctzd themselves, log text, or private
helpers directly.

## Rules

- No mocks: no `unittest.mock`, no `monkeypatch` of app code. The seams are the
  `ctzd.testing` fakes and parameters: pass dates in rather than reading the clock.
- No real source, network, or clock. Inside `fake_sources` an unfaked source
  raises `ctzd.SourceError`, so a test can never reach a real system.
- Build frames in code: five rows or fewer, only the columns the test is about,
  from a small builder function at the top of the test file.
- A fake returns what the loader would get from the source (same column names
  and types), so the loader's checks run too.
- Compare frames with `pd.testing.assert_frame_equal` after
  `reset_index(drop=True)`; floats with `pytest.approx(value, abs=1e-9)`.
- Name tests for the behavior: `test_a_bond_rated_twice_is_an_error`.
- A reported bug gets a failing test that reproduces it before the fix.
- While iterating, `uv run pytest tests/test_prices.py -q --no-cov` runs one
  file (the coverage floor only holds for the full run). Finish with
  `ctzd check --json`.

## `ctzd.testing`

| Fake | Effect |
|---|---|
| `fake_sources(pricing_db={"sql/prices.sql": frame})` | `ctzd.query("pricing_db", "sql/prices.sql", ...)` returns a copy of `frame`. |
| `by_params(function)` in place of the frame | `function(params)` builds the frame from the bind parameters. |
| `fake_sources(ratings_api={"GET /ratings": payload})` | `ctzd.rest("ratings_api")` gets `payload` as JSON for that method and path; other routes answer 404. |
| `by_request(function)` in place of the payload | `function(request: httpx.Request)` builds the payload; use it for query parameters and paging. |
| `fake_store(tmp_path)` | `write_table` and `read_table` use a temporary folder. |
| `outbox()` | Yields the list of `ctzd.EmailMessage` (`to`, `subject`, `body`, `attachments`) and `ctzd.TeamsMessage` (`source`, `title`, `text`) instead of sending. |

Several sources go in one `fake_sources(...)` call. One source fakes either SQL
files or REST routes, not both. Routes match method and path, never the query
string. Entering and leaving a fake clears every `@ctzd.cached` cache.

## Examples

`tests/test_prices.py`

```python
import datetime as dt
from collections.abc import Mapping

import ctzd
import httpx
import pandas as pd
import pytest

from app.data.loaders import load_prices, load_ratings
from app.logic.prices import sector_summary, with_rating

DAY = dt.date(2026, 11, 4)


def prices(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["isin", "sector", "clean_price"])
    return frame.astype({"isin": "str", "sector": "str", "clean_price": "float64"})


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([("X1", "Banks", 99.0), ("X2", "Banks", 101.0), ("X3", "Utilities", 100.0)], [("Banks", 2, 100.0), ("Utilities", 1, 100.0)]),
        ([("X1", "Utilities", 100.0), ("X2", "Banks", 98.0)], [("Banks", 1, 98.0), ("Utilities", 1, 100.0)]),
        ([], []),
    ],
)
def test_sector_summary_counts_and_averages_each_sector(rows: list[tuple[str, str, float]], expected: list[tuple[str, int, float]]) -> None:
    assert list(sector_summary(prices(rows)).itertuples(index=False, name=None)) == expected


def test_a_bond_rated_twice_is_an_error() -> None:
    ratings = pd.DataFrame({"isin": ["X1", "X1"], "rating": ["AA", "A"]})
    with pytest.raises(pd.errors.MergeError):
        with_rating(prices([("X1", "Banks", 99.0)]), ratings)


def prices_on(params: Mapping[str, object]) -> pd.DataFrame:
    return prices([("X1", "Banks", 99.0)]) if params["as_of"] == DAY else prices([])


def test_the_loader_passes_the_date_to_the_query() -> None:
    with ctzd.testing.fake_sources(pricing_db={"sql/prices.sql": ctzd.testing.by_params(prices_on)}):
        assert load_prices(DAY)["isin"].tolist() == ["X1"]
        assert load_prices(DAY + dt.timedelta(days=1)).empty


def test_a_missing_column_is_named() -> None:
    with ctzd.testing.fake_sources(pricing_db={"sql/prices.sql": prices([]).drop(columns="sector")}), pytest.raises(ctzd.DataError, match="sector"):
        load_prices(DAY)


def ratings_page(request: httpx.Request) -> object:
    if request.url.params.get("page") == "2":
        return {"items": [{"isin": "X2", "rating": "A"}], "next": None}
    return {"items": [{"isin": "X1", "rating": "AA"}], "next": "/ratings?page=2"}


def test_ratings_follow_every_page() -> None:
    with ctzd.testing.fake_sources(ratings_api={"GET /ratings": ctzd.testing.by_request(ratings_page)}):
        assert load_ratings()["rating"].tolist() == ["AA", "A"]
```
