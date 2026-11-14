---
name: ctzd-data
description: "Read data in a ctzd app through the department's kit: ctzd sources, calling a kit loader from data/, your own SQL in data/sql/ run with the kit's query, [sources].use in app.toml, sample rows and fixtures, ctzd dev --fake, ctzd env, ctzd exec to look at live data, ids as text and numbers as float64, and the store for job tables. Use whenever the app reads data, a page needs new columns, or a job writes or reads a table."
---

# Data in a ctzd app: use your kit

Every read goes through the department's kit, a package named `ctzd_<sbu>` (`ctzd_fi`, `ctzd_mas`; the app's
`pyproject.toml` lists it). The kit knows each source's connection, its secret and its quirks; the app never does.
All of the app's reads live in `src/app/data/`, and nothing else imports the kit's sources, `query` or `store`.

## What the kit has

- The **`sbu-<sbu>` skill** lists every source and loader of the kit, each loader's columns and an example call, and
  the department's own notes. Read it first.
- **`ctzd sources --json`** prints the same facts, and whether each source serves sample rows here and why.
- **`ctzd sources SOURCE --json`** lists the tables and views of a database source (`oracle`, `mssql`);
  **`ctzd sources SOURCE TABLE --json`** gives a table's columns, their types and its first rows. Those rows are
  real data: use them to learn the shape, and never copy them into fixtures, code or messages.

Pick the source whose description matches what the person asked for and tell them which data you will use, in their
words. If none matches, say so; never invent a source or a table. Every kit also has `sample`, made-up rows the
templates read: replace each use of it with a real source before the app ships.

## Two ways to read

`src/app/data/__init__.py` (the template's, with `sample` replaced by Fixed Income's real sources):

```python
from datetime import date

import pandas as pd
from ctzd_fi import fi_pricing, query


def prices(as_of: date) -> pd.DataFrame:
    """Every bond's published price at the close of `as_of`."""
    return fi_pricing.prices(as_of)


def watchlist_prices(as_of: date, sector: str) -> pd.DataFrame:
    """The desk's watchlist bonds of `sector`, priced at the close of `as_of`."""
    return query("fi_pricing", "watchlist_prices.sql", as_of=as_of, sector=sector)
```

1. **Call a loader.** `from ctzd_<sbu> import SOURCE`, then `SOURCE.LOADER(...)`, exactly as the skill's example
   shows. Prefer this whenever a loader returns what you need: the kit has tested it and brings its sample rows.
2. **Your own SQL**, only for a source whose kind is `oracle` or `mssql`. Write one statement in
   `src/app/data/sql/NAME.sql` and run it with the kit's `query(SOURCE, "NAME.sql", **binds)`. Binds go by name:
   `:as_of` for Oracle, `%(as_of)s` for SQL Server. Never build SQL from strings. Select only the columns the page
   uses, and filter and aggregate in SQL.

`src/app/data/sql/watchlist_prices.sql` (Oracle):

```sql
select p.isin, p.issuer, p.sector, p.clean_price, p.ytw, p.oas
from fi_prices.bond_prices p
join fi_prices.watchlist w on w.isin = p.isin
where p.as_of = :as_of and p.sector = :sector
```

A data function is a plain, synchronous function: a page awaits it through `ui.load` (`ctzd-pages`), and a job calls
it directly. Give each a docstring saying what it returns. Keep rules (sums, filters a person would describe, joins
of two reads) in `logic/`, not in `data/`.

## Name every source in app.toml

```toml
[sources]
use = ["fi_pricing"]
```

Only sources named here get credentials, on the desktop (`ctzd env`) and in the app's pods. `ctzd check` refuses a
name the kit does not have. Leave `sample` out once nothing reads it.

## What comes back

- **Every number is a float64**, counts and whole numbers too, with NaN where it is missing. Keep it that way: never
  round, never cast to a decimal, never format a number as text. The page and `ctzd_common.display` round once, when
  they show it. `ctzd check` refuses `ROUND`, decimal casts and `TO_CHAR` with a number format in SQL, and `round()`,
  `.round()` and number formats in Python.
- **Ids are text.** A column named `id` or ending in `_id` that holds numbers fails the read. In your own SQL select
  an id as text: `TO_CHAR(security_id) AS security_id` on Oracle, `CAST(security_id AS VARCHAR(40)) AS security_id`
  on SQL Server. Name other identifiers (ISIN, CUSIP, account numbers) as text too.
- **"Latest" is the newest date the source has**, not today: sources are stamped at the close, skip weekends and
  holidays, and the `sbu-<sbu>` skill says when each is current. Default a date filter to the last close, and show
  the date on the page.

## Sample rows

Every loader has sample rows in the kit. They are served instead of the real source:

- with `ctzd dev --fake`, in `ctzd check`, `ctzd render` and the tests, for every source;
- for a source with no credentials here (no `ctzd env` yet, or a source dev has no copy of), outside prod;
- for a source that cannot run on a desktop, on the desktop.

Sample rows ignore the arguments: a loader called for any date answers the same rows. When a page shows sample rows
it shows the sample-data banner by itself, and a table a job writes from them carries the same mark. An app that
only calls loaders writes no fixtures.

For **your own SQL** you write the sample rows: `fixtures/SOURCE/NAME.csv`, for `src/app/data/sql/NAME.sql` on
SOURCE, a header with exactly the columns the query returns and three to ten made-up rows. The template's
`fixtures/sample/curve.csv` is one. A file in `fixtures/SOURCE/LOADER.csv` also overrides a kit loader's sample rows,
when the page needs cases the kit's rows lack. Never put real data in fixtures: they are committed.

```csv
isin,issuer,sector,clean_price,ytw,oas
US0000000001,Contoso Corp,Industrial,99.5,0.0512,0.0141
US0000000002,Fabrikam Inc,Financial,101.25,0.0477,0.0098
```

## Real data on the desktop

1. `ctzd env --json` writes the dev credentials of the sources in `[sources].use`, read from the dev Key Vault with
   the person's own work sign-in. It is the only time they sign in to Azure: tell them a browser tab will ask for
   their work account. Run it again after you change `[sources].use`.
2. `ctzd dev --json` then runs the dashboard on real data where a source allows; the others still serve sample
   rows, with the banner.
3. To look at what a data function returns from the live source, with no browser:

```text
ctzd exec --json -- python -c "from datetime import date; from app.data import prices; print(prices(date(2026, 11, 12)).head())"
```

`ctzd exec` runs the command with the app's credentials, exactly as `ctzd dev` runs the dashboard. It is the only way
to look at live data: never open, print or source `.env`.

## Tables a job writes: the store

A job writes its results as tables of the app, and the dashboard reads them back, through the kit's `store`, from
`data/` like any other read:

```python
import pandas as pd
from ctzd_fi import store


def write(frame: pd.DataFrame, table: str) -> str:
    """Publish `frame` as the new version of the app's table `table`, and return the version."""
    return store.write_table(frame, table)


def sector_summary() -> pd.DataFrame:
    """The sector summary the morning job last wrote."""
    return store.read_table("sector_summary")
```

- `store.write_table(frame, NAME, allow_empty=False)` publishes a new version whole; readers see the old one until
  it is complete. An empty frame is refused unless `allow_empty=True`, for a real "nothing today".
- `store.read_table(NAME, columns=None)` reads the latest version. A table never written raises: run the job first
  (`ctzd run JOB --local` writes to `.ctzd/out/store/` here; `ctzd run JOB --env dev` in dev).
- Each environment has its own store, and an app reads only its own tables.

A read that takes more than a few seconds does not belong behind a page: a job reads it on a schedule and writes a
table, and the page reads the table (`ctzd-jobs`).

## pandas 3

- Copy-on-write: `frame["price"][mask] = 0` changes nothing. Write `frame.loc[mask, "price"] = 0`, or build a new
  column with `frame.assign(...)`. Never use `inplace=True`.
- Text columns have dtype `str`, not `object`; missing text is NaN, tested with `.isna()`.
- Pass `validate="many_to_one"` (or `one_to_one`, `one_to_many`) to every `merge`, so a duplicated key raises
  instead of multiplying rows.
- Compare a `datetime64` column with `pd.Timestamp(day)`, not a `date`.
