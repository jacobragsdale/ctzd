---
name: ctzd-data
description: "Read data in a ctzd app: ctzd.query with SQL files and bind parameters, ctzd.rest, @ctzd.cached, ctzd.require_columns, sources in app.toml, moving slow queries into a job and the table store, and pandas 3 pitfalls. Use whenever a loader reads or a job writes data."
---

# Data in a ctzd app

## Rules

- All reads live in loaders in `src/app/data/`. Datasets and jobs call loaders; pages read datasets.
- A source is a name such as `pricing_db` (names here are examples).
  `ctzd sources --json` lists the ones the platform has, each with its kind
  (`oracle`, `mssql`, `rest`), what it holds, and when its data is current. Use
  only those. List each in `app.toml`, then run `ctzd env --json`; only the
  platform team can add a source. When none matches what the person asked for,
  tell them; never invent a source.
- Before writing a query, look: `ctzd sources SOURCE --json` lists the tables and
  views the source's account can read, and `ctzd sources SOURCE TABLE --json` a
  table's columns with their types and its first five rows. The kind decides the
  binds: `:name` on `oracle`, `%(name)s` on `mssql`. The rows are real data: use
  them to learn the shape, never copy them into fixtures or messages.
- SQL lives in `src/app/data/sql/NAME.sql`, one statement per file. Never build
  SQL with f-strings, `+`, or `.format`: pass values as bind parameters.
- Select only the columns the app uses, and filter and aggregate in SQL, not in
  pandas after fetching everything.
- Check the columns the code relies on with `ctzd.require_columns` in the loader.
- Every number arrives as a `float64`, with `NaN` where it is missing: decimals,
  integers, and counts alike. Select ids, account numbers, and other numbers
  that name something as text: `TO_CHAR(security_id) AS security_id` on Oracle,
  `CAST(security_id AS VARCHAR(40)) AS security_id` on SQL Server.
- Never round a number or format it as text, in SQL or in Python: no `ROUND`,
  no casts to `NUMBER(p,s)` or `DECIMAL(p,s)`, no `round()` or `.round()`, no
  `f"{x:.2f}"`. The page shows each number to the decimals it needs, and
  `ctzd.display` writes numbers into emails and messages (see `ctzd-jobs`).
  `ctzd check` refuses rounding in the app's Python and SQL.
- Cache every loader a dataset calls with `@ctzd.cached`.
- "Today's" or "latest" data means the newest date the source has, not the
  clock's date: sources are stamped at close of business, skip weekends and
  holidays, and the platform's clock is UTC, already tomorrow in the evening
  in the Americas. Select it in SQL (`WHERE as_of_date = (SELECT MAX(as_of_date)
  FROM ...)`), or default a date picker to it, and show the date on the page.

```toml
[sources]
use = ["pricing_db", "ratings_api"]
```

## API (`import ctzd`)

| Call | Does |
|---|---|
| `ctzd.query(source, "sql/x.sql", params=None) -> DataFrame` | Runs `src/app/data/sql/x.sql` on an Oracle or SQL Server source. Binds: `:name` for Oracle, `%(name)s` for SQL Server; `params` is a dict of names to values (`str`, numbers, `datetime.date`). |
| `ctzd.rest(source)` | Client for a REST source with auth, a timeout, and 3 attempts on connection errors, 429, and 5xx. |
| `.get_frame(path, params=None, *, records=None)` | GET JSON rows as a frame. `records="items"` names the key holding the list; nested objects become dotted columns (`issuer.name`). |
| `.get_all_pages(path, params=None, *, records, next_key="next")` | Follows the link in `next_key` until it is empty; concatenates the rows. |
| `.get_json(path, params=None) -> object` / `.post_json(path, body, params=None) -> object` | Raw JSON. It is `object`: narrow it with `isinstance` before use. |
| `@ctzd.cached(ttl="15m")` | Caches the result per argument values, in this process, for `ttl` (`"30s"`, `"15m"`, `"2h"`, `"1d"`, seconds, or a `timedelta`). Arguments must be hashable: `str`, numbers, dates, tuples. Pass `("a", "b")`, never a list or dict. |
| `ctzd.require_columns(frame, {"col": "str"}, what="prices")` | Raises `ctzd.DataError` naming every missing column or wrong dtype. Families: `str`, `float` (every number), `bool`, `datetime`, `any`. Returns the frame. |
| `ctzd.write_table(frame, "name", allow_empty=False) -> str` | Publishes a new version of this app's table (jobs only). An empty frame raises `ctzd.StoreError` unless `allow_empty=True`. |
| `ctzd.read_table("name", app=None, sbu=None, columns=None)` | Latest version of a table; `app=`/`sbu=` read another app's table. |

Errors: `ctzd.SourceError` (source missing or misused), `ctzd.DataError`,
`ctzd.StoreError`; all subclass `ctzd.CtzdError`. Let them raise.

What comes back: Oracle column names that are all upper case (unquoted
identifiers) arrive lower case, so `select isin, price` gives `isin` and
`price`; quoted names and SQL Server names arrive as written. Every number
arrives as `float64`, date columns as `datetime64`, text as `str`. A
whole-number column holding a value beyond 9,007,199,254,740,992 (2**53) fails
the query with `ctzd.DataError`, because a float64 cannot hold it exactly: it is
almost always an id, so select it as text.

## Datasets: what the page reads

`src/app/api/datasets.py` declares each dataset: a function of the page's parameters that
calls loaders and logic and returns a frame, and a row type naming the columns the page gets.

```python
import datetime as dt

import ctzd
import pandas as pd

from app.data.loaders import load_prices
from app.logic.summary import sector_summary


class SectorPrice(ctzd.Row):
    """One sector's bonds and average clean price."""

    sector: str
    bonds: ctzd.Count
    avg_price: ctzd.Price


@ctzd.dataset(SectorPrice)
def sector_prices(as_of: dt.date | None = None) -> pd.DataFrame:
    """Bonds and average clean price per sector, for the day the page asks for."""
    return sector_summary(load_prices(as_of))
```

- The function's arguments are the page's parameters: `str`, `int`, `float`, `bool`,
  `dt.date`, each with a default when the page may leave it out. A dataset only reads.
- The row type lists the columns in the order the page shows them, and the frame holds
  exactly those columns: select them (`frame[["sector", "bonds", "avg_price"]]`) when the
  logic returns more. A column missing, extra, or of another type fails the request with a
  message naming it. Numbers become floats and missing or infinite values blanks on the way.
- Column kinds say how the page rounds a number: `ctzd.Number` (2 decimals), `ctzd.Count`
  (none), `ctzd.Price`, `ctzd.Price32` (Treasury 32nds), `ctzd.Pct` and `ctzd.Bps` (both
  fractions: 0.0525 is 5.25% and 525 bp), and `Annotated[float | None, ctzd.money("USD")]`.
  Text is `str`, dates `dt.date`.
- The page reads it at `/api/datasets/sector-prices` through the generated client, as Arrow
  for parallax (see `ctzd-pages`). `ctzd dev` and `ctzd check` regenerate the client after
  every change here.
- Test a dataset through the app: `TestClient(ctzd.api.asgi("app.api.datasets"))` from
  `starlette.testclient`, with `ctzd.testing.fake_sources` around it (see `ctzd-testing`).

## Fake rows: fixtures/

`fixtures/SOURCE/NAME.csv` holds made-up rows for `src/app/data/sql/NAME.sql` on `SOURCE`, with
the same column names as the real query. Each column arrives as the loader's
`ctzd.require_columns` declares it, so declare every id `"str"` there and an account `00123456` or a
SEDOL stays text. Without a declaration, ISO dates (`2026-11-06`) arrive as dates, numbers as
floats, and a column named `id` or ending in `_id`, or a number written with a leading zero, as
text.

A REST source's fake answers live in `fixtures/SOURCE/rest.json`, one entry per request the loaders
make, holding the JSON the real API returns:

```json
{"GET /ratings": {"items": [{"isin": "US0001", "rating": "AA"}], "next": null}}
```

`ctzd dev --fake` serves them in place of every source, and a source, SQL file, or REST route
without fake rows fails, so a fake page never reaches real data. `ctzd add dataset --source` writes
a CSV to start from. Use them in tests too:
`ctzd.testing.fake_sources(**ctzd.testing.fixture_sources(Path("fixtures")))`. Never put real
client data in them: they are committed.

## Loaders

`src/app/data/sql/trades.sql` (SQL Server)

```sql
SELECT CAST(trade_id AS VARCHAR(40)) AS trade_id, isin, quantity
FROM dbo.trades
WHERE trade_date = %(trade_date)s AND desk = %(desk)s
```

`src/app/data/loaders.py`

```python
import datetime as dt

import ctzd
import pandas as pd


@ctzd.cached(ttl="15m")
def load_trades(trade_date: dt.date, desk: str) -> pd.DataFrame:
    frame = ctzd.query("trades_db", "sql/trades.sql", {"trade_date": trade_date, "desk": desk})
    return ctzd.require_columns(frame, {"trade_id": "str", "isin": "str", "quantity": "float"}, what="trades")


@ctzd.cached(ttl="1h")
def load_ratings() -> pd.DataFrame:
    frame = ctzd.rest("ratings_api").get_all_pages("/ratings", {"page_size": 500}, records="items")
    return ctzd.require_columns(frame, {"isin": "str", "rating": "str"}, what="ratings")


@ctzd.cached(ttl="1h")
def load_rating_scale() -> tuple[str, ...]:
    payload = ctzd.rest("ratings_api").get_json("/scale")
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        msg = f"/scale should return a list of strings, got {type(payload).__name__}"
        raise ctzd.DataError(msg)
    return tuple(str(item) for item in payload)


@ctzd.cached(ttl="10m")
def _sector_summary() -> pd.DataFrame:
    frame = ctzd.read_table("sector_summary")
    return ctzd.require_columns(frame, {"sector": "str", "bonds": "float", "avg_price": "float"}, what="sector_summary")


def load_sector_summary() -> pd.DataFrame:
    """The job's latest summary; no rows until the job has run once, so the page can say so."""
    try:
        return _sector_summary()
    except ctzd.StoreError:
        # Not cached: the page shows the data as soon as the job has run.
        return pd.DataFrame({"sector": pd.Series(dtype="str"), "bonds": pd.Series(dtype="float64"), "avg_price": pd.Series(dtype="float64")})
```

Counts arrive as `float64` too (`COUNT(*)`), and so does every number in a fake
source's frame, a REST response, and a table the store returns, so test frames
and live data agree. Declare numbers `"float"` in `require_columns`.

The types in these examples are illustrations: before the first deploy, print
`frame.dtypes` from the live source (below) and declare what comes back, or the
smoke test fails on a type the tests never saw.

To see what a loader returns from the live source, without a browser:
`ctzd exec --json -- python -c "from app.data.loaders import load_prices; print(load_prices().head())"`.
Never source or open `.env` to do this.

Pass the column map to `ctzd.require_columns` inline, as above. Kept in a
variable first, it is typed `dict[str, str]`, and `ctzd check`'s types step
rejects it.

Call `ctzd.rest(...)` inside the loader, never at module level, so test fakes apply.
A bind holds one value, not a list: for "these sectors", load the day's rows
and filter in a logic function, or write one bind per value.

## Slow queries go into a job

`ctzd.query` logs `Slow query: ... took N s` when a query takes more than 3
seconds. A dashboard must not wait on that. Move it:

1. A job (`ctzd add job`, see `ctzd-jobs`) runs the query on a schedule and calls
   `ctzd.write_table(summary, "sector_summary")`. Writes are atomic: readers see
   the old version until the new one is complete.
2. The dashboard's loader reads it with `ctzd.read_table` (as `load_sector_summary` above).
3. The page must render before the job's first run: the loader returns the
   table's columns with no rows (as `load_sector_summary` above), and the page,
   when its dataset has no rows, shows a plain message such as "The first
   refresh runs at 06:00 UTC; the figures appear after it." A dataset that
   raises fails the post-deploy check, and the release does not go live.
4. Deploy, then run the job once (`ctzd run JOB --wait --json`) so the page has
   data straight away. Jobs only run the code of a release that went live.

Locally, `ctzd run JOB --local` writes to `.ctzd/out/store/`. A table not
written locally is read from production when `ctzd env` provided read access.

## pandas 3 pitfalls

- Copy-on-write: chained assignment such as `prices["price"][mask] = 0`
  changes nothing, and fails tests with `ChainedAssignmentError`. Write
  `prices.loc[mask, "price"] = 0`, or build a new column with
  `prices = prices.assign(cheap=prices["clean_price"] < THRESHOLD)`.
  Editing a filtered frame never changes the original. Do not use `inplace=True`.
- Text columns have dtype `str`, not `object`: `dtype == object` is now false.
  Use `ctzd.require_columns` or `pd.api.types.is_string_dtype`. Missing text is
  `NaN`; test with `.isna()`.
- Pass `validate="many_to_one"` (or `one_to_one`, `one_to_many`) to every
  `merge`; a duplicated key then raises `pd.errors.MergeError` instead of
  silently multiplying rows.
- Floats are right for prices, amounts, and yields. Never round them: the page
  and `ctzd.display` round once, when they show a number.
- Compare a `datetime64` column with `pd.Timestamp(as_of)`, not a `date`.
