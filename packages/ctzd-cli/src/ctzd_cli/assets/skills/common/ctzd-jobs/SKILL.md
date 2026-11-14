---
name: ctzd-jobs
description: "Scheduled jobs in a ctzd app: a module in src/app/jobs/ with main() -> int, its [[jobs]] entry in app.toml (cron schedule, timezone, size, retries), ctzd run JOB --local, email and Slack with ctzd_common, where messages go in each environment, writing and reading tables with the kit's store, schedules running in prod only, and ctzd run JOB --env dev. Use for 'every Monday', 'each morning', 'send me', 'email the team', 'post to Slack', or a slow read a page should not wait on."
---

# Jobs in a ctzd app

A job is a module, `src/app/jobs/NAME.py`, with `def main() -> int:`. Airflow calls `main()` in the app's image on
the schedule in `app.toml`. A job app has only jobs; a dash app may have any number beside its dashboard, and often
should: a job that reads something slow each morning and writes a table the page reads keeps the page fast.

The job template's `jobs/daily_positions.py` is a complete job: it reads through `data/`, checks and transforms with
`logic/`, writes two tables and emails a summary. Read it before you write one.

## Adding a job

1. The reads and writes in `src/app/data/` (`ctzd-data`), the rules in `src/app/logic/`.
2. `src/app/jobs/NAME.py` with `main() -> int`, as below. NAME is lowercase letters, digits and underscores.
3. Its `[[jobs]]` entry in `app.toml`, with the same name.
4. Tests of its rules, and of `main()` on the sample rows (`ctzd-testing`).
5. `ctzd run NAME --local --json`, read what it wrote and kept, then `ctzd check --json`.

When the person's app is the job template, replace `daily_positions` (module, test and `[[jobs]]` entry) rather than
adding beside it, and delete the module's `ctzd: sample` first line.

## app.toml

```toml
[[jobs]]
name = "sector_summary"
schedule = "0 7 * * 1-5"         # minute hour day-of-month month weekday
timezone = "Europe/London"       # an IANA zone: the schedule is the person's local time, clock changes included
size = "m"                       # s | m | l: the memory and CPU of its pod
retries = 1                      # 0 to 5; Airflow retries a failed run, then emails the owners
timeout_minutes = 60
```

Ask where the person is when the time matters, and write their local time as it is: 07:00 in London is
`"0 7 * * 1-5"` with `timezone = "Europe/London"`, all year. Never add a second run or a clock check for daylight
saving. Without `timezone` the schedule is UTC.

| They say | `schedule` |
|---|---|
| weekdays at 7 | `0 7 * * 1-5` |
| every day at 6:30 | `30 6 * * *` |
| Mondays at 8 | `0 8 * * 1` |
| the first of the month at 7 | `0 7 1 * *` |
| every 4 hours | `0 */4 * * *` |

## A job

`src/app/jobs/sector_summary.py`:

```python
"""Each weekday morning: the sector summary of the last close, written to the app's table `sector_summary` and
emailed to the desk."""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ctzd_common import display, email, slack

from app.data import holdings, write
from app.logic import positions as rules

DESK = ZoneInfo("Europe/London")
SATURDAY = 5
TO = ("desk-team@contoso.com",)
CHANNEL = "fi-desk"
DASHBOARD = "https://fi-bond-prices.ctzd.contoso.com"
USD = display.Format("money", 0, currency="USD")
_LOG = logging.getLogger(__name__)


def last_close(today: date) -> date:
    day = today - timedelta(days=1)
    while day.weekday() >= SATURDAY:
        day -= timedelta(days=1)
    return day


def run(day: date) -> int:
    held = holdings(day)
    if held.empty:
        _LOG.error("No holdings for %s yet; nothing written.", day.isoformat())
        return 1
    sectors = rules.by_sector(rules.positions(held, ""))
    write(sectors, "sector_summary")
    table = email.Table(sectors, {"market_value": USD, "dv01": USD, "pnl": USD}, title=f"Sectors at the close of {day.isoformat()}")
    total = display.money(float(sectors["market_value"].sum()), "USD", decimals=0)
    email.send(list(TO), f"Sector summary for {day.isoformat()}", f"Market value {total}.", table=table, attachments={"sector_summary.xlsx": table}, link=DASHBOARD)
    slack.post(CHANNEL, f"The sector summary for {day.isoformat()} is out: market value {total}.", table=table)
    return 0


def main() -> int:
    return run(last_close(datetime.now(DESK).date()))
```

- **The exit code is the contract.** Return 0 only when the work is done; return 1, or let the error raise, when it
  is not. Airflow then retries and emails the owners. Never catch an error to return 0, and never write your own
  failure email.
- **`run(day)` takes the date**, so tests can fix it; only `main()` reads the clock. Label messages with the date of
  the data, not the day the job ran.
- **A rerun gives the same result.** Compute the whole table from the inputs for one date and publish it whole;
  never append to the last version or depend on what the last run left.
- **Write first, send last**, so a failed run never sends half a result. A retry sends again.
- **Reuse the dashboard's `data/` and `logic/`**: a job holds no rules of its own.
- Log with `logging` and `%s` arguments, never `print`. A job is synchronous: no `async`, no `ui.load`, no `ui`.

## Email and Slack

Only `pages` and `jobs` send messages.

- `email.send(to, subject, body, *, table=None, attachments=None, link=None)`: `to` is one address or a list.
  `table` is written into the body as the page would show it. `attachments` maps a file name ending in `.xlsx` or
  `.csv` to a frame or a `Table`: an `.xlsx` keeps every number raw and shows it through Excel's formats, with a
  frozen header and a filter. `link` adds "See it live".
- `slack.post(channel, text, *, table=None)`: `channel` is the channel's name, with or without `#`; the platform's
  bot must be in it. Ask the person which channel; never guess.
- `email.Table(frame, formats, title=None)`: `formats` maps each number column to a
  `display.Format(kind, decimals, currency=None, step=None)`, `kind` being `"number"`, `"pct"`, `"bps"`, `"money"`,
  `"price"` or `"price32"`: the same formats a page names with `ui.py`.
- A number in a subject, a body or a Slack text goes through `ctzd_common.display`, which rounds as the page does:
  `display.number(value, decimals=0)`, `display.money(value, "USD")`, `display.percent(value, decimals=1)`,
  `display.bps(value)`, `display.price(value, decimals=3)`, `display.price32(value)`. Never `round()` it or write
  `f"{value:.2f}"`: `ctzd check` refuses both.

Where a message goes depends on where the job runs, so a job is safe to run anywhere:

| Where | Email | Slack |
|---|---|---|
| this desktop (`ctzd run --local`, tests) | kept in `.ctzd/outbox/` as `email-*.eml`, never sent | kept as `slack-*.json`, never sent |
| dev | sent only to the app's owners, marked `[dev]`, saying who it would go to | a direct message to each owner, marked `[dev]` |
| prod | to whoever the code names | to the channel |

The owners are `app.toml`'s `[app].owners`. `ctzd check` and the deploy's smoke test run each job with messages off.

## Tables

Write with the kit's `store.write_table(frame, NAME)` and read with `store.read_table(NAME)`, both wrapped in
`data/` (`ctzd-data`; the job template's `data/__init__.py` has `write` and `read`). A table name is lowercase
letters, digits and underscores. An empty frame is refused unless `allow_empty=True`, for a real "nothing today".
Each environment has its own store: `--local` writes to `.ctzd/out/store/`, and dev and prod to theirs. A table
written from sample rows carries the mark, and a page that shows it shows the sample-data banner.

A page that reads a job's table must still work before the job has run in that environment: on a fresh dev or prod
deploy the table does not exist yet. Until it does, `store.read_table` raises, and the page tells the person
"Table ... has never been written here", which means nothing to them. So after the first deploy with a new job, run
it once in dev (`ctzd run NAME --env dev --wait --json`, below) before you send the review link, and once in prod
right after the first `ctzd promote`. Say on the page when the figures refresh ("updated each weekday by 07:00").

## Running a job

| Where | Command | What happens |
|---|---|---|
| here | `ctzd run NAME --local --json` | runs `main()` with the app's credentials; `exit_code`, the last lines of `output`; messages kept, tables in `.ctzd/out/store/` |
| dev | `ctzd run NAME --env dev --json` | after a deploy: runs it once in dev's Airflow now and reports the result and the Airflow link |
| prod | `ctzd run NAME --env prod --json` | runs the live job now, out of its schedule, with no approval |

Add `--wait` to wait for the run to finish; a failed run returns its last log lines.

**Schedules run in prod only.** In dev a job has no schedule: it runs only when `ctzd run NAME --env dev` starts it,
so a dev deploy never emails anyone twice or queries a source on a timer. After `ctzd promote` the schedule starts,
and the owners get an email with each job's Airflow link. Give the person that link: Airflow is where they see each
run.
