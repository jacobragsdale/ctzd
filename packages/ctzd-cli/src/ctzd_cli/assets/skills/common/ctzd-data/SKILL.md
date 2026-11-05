---
name: ctzd-data
description: "Read data in a ctzd app: ctzd.query with SQL files and bind parameters, ctzd.rest, @ctzd.cached, ctzd.require_columns, sources in app.toml, moving slow queries into a job and the table store, and pandas 3 pitfalls. Use whenever a loader reads or a job writes data."
---

# Data in a ctzd app

## Rules

- All reads live in loaders in `src/app/data/`. Pages, callbacks, and jobs call loaders.
- A source is a name such as `pricing_db` (names here are examples).
  `ctzd sources --json` lists the ones the platform has. Use only those, and
  the ones the SBU skill or the person names. List each in `app.toml`, then run
  `ctzd env --json`; it fails with "does not exist" for an unknown name, and
  only the platform team can add one. Tell the person; never invent a source.
- SQL lives in `src/app/data/sql/NAME.sql`, one statement per file. Never build
  SQL with f-strings, `+`, or `.format`: pass values as bind parameters.
- Select only the columns the app uses, and filter and aggregate in SQL, not in
  pandas after fetching everything.
- Check the columns the code relies on with `ctzd.require_columns` in the loader.
- Cache every dashboard loader with `@ctzd.cached`.

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
| `ctzd.require_columns(frame, {"col": "str"}, what="prices")` | Raises `ctzd.DataError` naming every missing column or wrong dtype. Families: `str`, `float`, `int`, `number`, `bool`, `datetime`, `any`. Returns the frame. |
| `ctzd.write_table(frame, "name", allow_empty=False) -> str` | Publishes a new version of this app's table (jobs only). An empty frame raises `ctzd.StoreError` unless `allow_empty=True`. |
| `ctzd.read_table("name", app=None, sbu=None, columns=None)` | Latest version of a table; `app=`/`sbu=` read another app's table. |

Errors: `ctzd.SourceError` (source missing or misused), `ctzd.DataError`,
`ctzd.StoreError`; all subclass `ctzd.CtzdError`. Let them raise.

What comes back: Oracle column names that are all upper case (unquoted
identifiers) arrive lower case, so `select isin, price` gives `isin` and
`price`; quoted names and SQL Server names arrive as written. Decimal numbers
arrive as `float64`, date columns as `datetime64`, text as `str`.

## Loaders

`src/app/data/sql/trades.sql` (SQL Server)

```sql
SELECT trade_id, isin, quantity
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
    return ctzd.require_columns(frame, {"trade_id": "str", "isin": "str", "quantity": "number"}, what="trades")


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
def load_sector_summary() -> pd.DataFrame:
    frame = ctzd.read_table("sector_summary")
    return ctzd.require_columns(frame, {"sector": "str", "bonds": "int", "avg_price": "float"}, what="sector_summary")
```

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
3. Deploy the job first and run it (`ctzd run JOB --wait --json`), and only then deploy
   the page that reads the table: a page reading a table that was never
   written fails the post-deploy check.

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
- Floats are fine for prices, amounts, and yields. Round only for display.
- Compare a `datetime64` column with `pd.Timestamp(as_of)`, not a `date`.
