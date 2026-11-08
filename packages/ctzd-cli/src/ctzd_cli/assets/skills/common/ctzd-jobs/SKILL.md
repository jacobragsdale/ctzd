---
name: ctzd-jobs
description: "Scheduled jobs in a ctzd app: ctzd add job with a cron schedule in the person's time zone, main() -> int and exit codes, ctzd.write_table, email and Teams messages, local and Airflow runs, and safe reruns. Use for 'every Monday', 'each morning', 'send me', 'email the team', or a slow query that must be precomputed."
---

# Jobs in a ctzd app

A job is `src/app/jobs/NAME.py` with `main() -> int`. Airflow calls its `main()`
in the app's image on the schedule in `app.toml`.

## Rules

- Add a job with `ctzd add job NAME --schedule "CRON" --timezone ZONE --json`
  (`--size s|m|l`, default `m`). NAME is lowercase letters, digits, and underscores. It writes
  `src/app/jobs/NAME.py`, `tests/test_job_NAME.py`, and a `[[jobs]]` entry.
- The schedule is five-field cron (minute, hour, day of month, month, weekday)
  in the job's `timezone`, an IANA name such as `Europe/London` or
  `America/New_York`; the default is `UTC`. Ask where the person is when the
  time matters, and write their local time as it is: 07:00 in London is
  `"0 7 * * 1-5"` with `--timezone Europe/London`, all year. Never add a second
  run or a clock check to follow daylight saving.
- The exit code is the contract. Return 0 only when the work is done. Return 1
  (or let the exception raise) when it is not: Airflow retries (`retries` in
  `app.toml`), then emails the app's owners. Never catch an error to return 0,
  and never write your own failure email.
- Reuse the dashboard's loaders and logic; a job holds no rules of its own.
- A rerun must give the same result. Compute the whole table from the inputs
  for one date and publish it with `ctzd.write_table`; never append to the
  previous version or depend on what the last run left.
- Publish the table first and send messages last, so a failed run never sends
  a half result. A retry sends the messages again. Log with `logging`, not `print`.
- Change `schedule`, `timezone`, `size`, `retries` (0-5), or `timeout_minutes` in the job's `[[jobs]]` entry.

| Schedule | Cron |
|---|---|
| Weekdays 07:00 | `0 7 * * 1-5` |
| Every day 06:30 | `30 6 * * *` |
| Mondays 08:00 | `0 8 * * 1` |
| First of the month 07:00 | `0 7 1 * *` |
| Every 4 hours | `0 */4 * * *` |

## A job

`src/app/jobs/sector_summary.py`

```python
"""Job `sector_summary`: each weekday morning, publish the sector summary and email it."""

import datetime as dt
import logging

import ctzd

from app.data.loaders import load_prices
from app.logic.prices import sector_summary

_LOG = logging.getLogger(__name__)
RECIPIENTS = ("desk-team@contoso.com",)


def run(as_of: dt.date) -> int:
    prices = load_prices(as_of)
    if prices.empty:
        _LOG.error("No prices for %s yet; nothing published.", as_of)
        return 1
    summary = sector_summary(prices)
    version = ctzd.write_table(summary, "sector_summary")
    ctzd.notify.email(RECIPIENTS, f"Sector summary {as_of:%d %b %Y}", "The sector summary is attached.", attachments={"sector_summary.xlsx": summary})
    ctzd.notify.teams("desk_channel", f"The sector summary for {as_of:%d %b %Y} is ready.", title="Sector summary")
    _LOG.info("Published %d sectors as version %s.", len(summary), version)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return run(dt.datetime.now(dt.UTC).date())


if __name__ == "__main__":
    raise SystemExit(main())
```

`run(as_of)` takes the date so tests can fix it; only `main()` reads the clock.
Label messages with the date of the data they carry, not the day the job ran: a
morning job that reports the latest prices says the previous business day.

## Messages and tables

- `ctzd.notify.email(to, subject, body, attachments={"name.xlsx": frame})`:
  `to` is one address or a sequence. Attachment names end in `.xlsx` or `.csv`.
- `ctzd.notify.teams(source, text, title=None)`: `source` is a `teams` source,
  a channel webhook the platform team adds. Ask the person which channel; never
  guess the name.
- A number in a subject, a body, or a Teams message goes through `ctzd.display`,
  which rounds it the way the page does: `ctzd.display.number(total, decimals=0)`,
  `ctzd.display.money(par, "USD")`, `ctzd.display.percent(weight, decimals=1)` and
  `ctzd.display.bps(spread)` (rates and spreads are fractions: 0.0525 is 5.25%
  and 525 bp). Never `round()` it or write `f"{x:.2f}"`; `ctzd check` refuses
  both. Attachments carry the raw numbers, and Excel shows them.
- `ctzd.write_table(frame, "name")` refuses an empty frame. Pass
  `allow_empty=True` only when no rows is a real answer ("no breaches today").
- The dashboard reads the table through a loader that calls
  `ctzd.read_table("name")` (see `ctzd-data`).

## Running

| Where | Command | Result |
|---|---|---|
| Here | `ctzd run NAME --local --json` | `exit_code`, `output` (last 40 lines). Messages are saved to `.ctzd/out/outbox/` (`email-*.eml`, `teams-*.json`), tables to `.ctzd/out/store/`. Nothing is sent. Run `ctzd env --json` first. |
| Airflow | `ctzd run NAME --wait --json` | Runs the deployed job now and waits: `state` is `success` or `failed`, `url` is the run page for the person. Deploy first. |
| Logs | `ctzd logs --job NAME --json` | The latest Airflow run's log. |

After adding or changing a job, `ctzd deploy --json`; its `dags` field maps each
job to its Airflow page. If a dashboard reads the job's table, deploy and run
the job before the page that reads it goes live.

## Tests

The starter test runs `main()` against `ctzd.testing.fake_store`. Replace it
with tests of `run` against fixed dates (see `ctzd-testing`).

`tests/test_job_sector_summary.py`

```python
import datetime as dt
from pathlib import Path

import ctzd
import pandas as pd

from app.jobs.sector_summary import run

DAY = dt.date(2026, 11, 4)


def prices() -> pd.DataFrame:
    return pd.DataFrame({"isin": ["XS0000000001", "XS0000000002"], "sector": ["Banks", "Utilities"], "clean_price": [99.5, 101.25]})


def test_the_job_publishes_and_emails_the_summary(tmp_path: Path) -> None:
    with ctzd.testing.fake_sources(pricing_db={"sql/prices.sql": prices()}), ctzd.testing.fake_store(tmp_path), ctzd.testing.outbox() as sent:
        assert run(DAY) == 0
        assert ctzd.read_table("sector_summary")["sector"].tolist() == ["Banks", "Utilities"]
    email, _teams = sent
    assert isinstance(email, ctzd.EmailMessage)
    assert [item.filename for item in email.attachments] == ["sector_summary.xlsx"]


def test_a_day_without_prices_fails_and_sends_nothing(tmp_path: Path) -> None:
    with ctzd.testing.fake_sources(pricing_db={"sql/prices.sql": prices().iloc[0:0]}), ctzd.testing.fake_store(tmp_path), ctzd.testing.outbox() as sent:
        assert run(DAY) == 1
    assert sent == []


def test_a_rerun_publishes_the_same_table(tmp_path: Path) -> None:
    with ctzd.testing.fake_sources(pricing_db={"sql/prices.sql": prices()}), ctzd.testing.fake_store(tmp_path), ctzd.testing.outbox():
        run(DAY)
        first = ctzd.read_table("sector_summary")
        run(DAY)
        pd.testing.assert_frame_equal(ctzd.read_table("sector_summary"), first)
```
