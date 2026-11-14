---
name: ctzd-testing
description: "What to test in a ctzd app and how: logic tests on tiny frames built in code, each page's callbacks called on the sample rows, each job's run, the 70% branch-coverage gate, warnings as errors, and what ctzd check runs besides the tests. Use when writing, fixing or reviewing tests, or when the tests step of ctzd check fails."
---

# Testing a ctzd app

## The gate

`ctzd check`, and so every deploy, runs pytest on the sample rows and fails unless the tests cover at least 70% of
the branches in `src/app/logic/` (set in the locked `pytest.toml`). A warning the app or its tests cause fails the
test, and so does pandas' `ChainedAssignmentError`; a dependency's deprecation is listed instead. The 70% is a
floor, not the goal: each test asserts something a person would notice if it broke. Never write a test that only
runs code, weaken an assertion until it passes, or skip or xfail a failing test. Fix the code or the test.

Besides pytest, `ctzd check` drives every page on the sample rows (its `pages` step) and runs each job's `main()`
once (its `jobs` step). Tests add what those cannot see: that the numbers are right.

## How tests run

`tests/conftest.py`, from the template, sets the environment `ctzd check` gives the tests: every source serves its
sample rows (`CTZD_FAKE=1`), the app's own `fixtures/` override the kit's, the store is `.ctzd/out/test-store/`, and
messages are kept in `.ctzd/out/test-outbox/`. So a test never reaches a real source, sends nothing and writes
nothing that matters. Keep that file as it is. Tests import the app (`app.logic`, `app.pages`, `app.jobs`), never
`ctzd_kit` or the kit's sources.

## What to test, in order

1. **Logic.** Every function in `src/app/logic/`, on frames of five rows or fewer built in the test file, with only
   the columns the test is about: one normal case, each rule's boundary (at the limit and just past it), and the
   empty frame. Use `pytest.mark.parametrize` for several cases of one rule. This is where the coverage comes from.
2. **Each page's callbacks**, called on the sample rows: each returns a value for every output it names.
3. **Each job**: its rules, and `main()` (or `run(day)`) on the sample rows returning 0.

The template's tests are the examples: `tests/test_positions.py` (logic), `tests/test_pages.py` (callbacks) and,
in a job app, `tests/test_daily_positions.py`.

```python
"""The sectors page's callback, on the sample rows."""

import asyncio

from app.pages import sectors


def test_the_sectors_callback_answers_its_stat_chart_and_grid() -> None:
    total, chart, rows = asyncio.run(sectors.show(sectors.last_close().isoformat(), "Credit"))
    assert str(total).startswith("$")
    assert chart is not None
    assert isinstance(rows, list)
    assert rows
```

A callback is `async`, so a test calls it with `asyncio.run(...)`. `ui.load` works outside Dash, so nothing else is
needed.

```python
import pandas as pd
import pytest

from app.logic import positions as rules


def holdings(*books: str) -> pd.DataFrame:
    """One holding at par in each of `books`, with only the columns `positions` reads."""
    return pd.DataFrame({"book": list(books), "par": 1_000_000.0, "price": 100.0, "prior_price": 100.0, "coupon": 0.0, "duration": 1.0})


@pytest.mark.parametrize(("book", "kept"), [("Credit", 1), ("", 2), ("Municipals", 0)])
def test_a_book_keeps_only_its_own_holdings(book: str, kept: int) -> None:
    assert len(rules.positions(holdings("Credit", "Core Rates"), book)) == kept
```

## Rules

- No mocks: no `unittest.mock`, no `monkeypatch` of app code. The seams are the sample rows and parameters: pass
  dates in rather than reading the clock (a job's `run(day)`, `ctzd-jobs`).
- Build frames in code, from a small builder function at the top of the file; never read the sample rows' CSV
  files in a logic test.
- Compare frames with `pd.testing.assert_frame_equal` after `reset_index(drop=True)`, floats with
  `pytest.approx`.
- Name tests for the behaviour: `test_a_bond_rated_twice_is_an_error`.
- A reported bug gets a failing test that reproduces it before the fix.
- While iterating, `uv run pytest tests/test_positions.py -q --no-cov` runs one file (the coverage floor only holds
  for the whole run). Finish with `ctzd check --json`.
- Do not test Dash, pandas, the kit or `ui.py` themselves, log text, or private helpers directly.
