---
name: ctzd-layout
description: "Where code goes in a ctzd app (src/app/data, logic, ui, jobs, tests, app.toml), the import rules ctzd check enforces, and worked examples of splitting a request into loader, logic, and page. Use before adding, moving, or splitting code."
---

# ctzd app layout

## Where things go

| Path | Holds | May import |
|---|---|---|
| `src/app/data/*.py` | Loaders: every read of a database, REST API, or table. | `ctzd`, pandas. Not `app.logic`, `app.ui`, `app.jobs`, streamlit, dash. |
| `src/app/data/sql/*.sql` | One query per file, bind parameters only. | |
| `src/app/logic/*.py` | Pure functions: frames and values in, frames and values out. Every business rule, filter, ranking, and join. | pandas, numpy, the standard library. Not `ctzd`, `app.data`, streamlit, dash, httpx, oracledb, mssql_python. |
| `src/app/ui/main.py`, `src/app/ui/pages/` | The page: inputs, loader calls, logic calls, display. | `app.data`, `app.logic`, `ctzd`, the UI framework. Not `app.jobs`, httpx, oracledb, mssql_python. |
| `src/app/jobs/NAME.py` | One scheduled job per module, each with `main() -> int`. | `app.data`, `app.logic`, `ctzd`. Not `app.ui`. |
| `tests/` | Tests for all of the above. | Anything. |
| `app.toml` | Name, owners, size, `[sources].use`, `[[jobs]]`. Edit it directly. | |
| `pyproject.toml` | Dependencies only, through `uv add PACKAGE`. | |

Every new folder under `src/app/` needs an empty `__init__.py`. Replace the
starter's `load_demo` and `with_share`, and their tests, once real code exists.

Never edit platform-owned files: `AGENTS.md`, `.agents/`, `Dockerfile`,
`Jenkinsfile`, `ruff.toml`, `ty.toml`, `.importlinter`, `pytest.toml`,
`.pre-commit-config.yaml`, `.gitignore`, `.gitattributes`, `.dockerignore`,
`.python-version`, `.ctzd/`. `ctzd check` puts them back.

## The import rules

`.importlinter` holds four contracts; `ctzd check` reports them as the `layers` step:

1. **Layers.** `app.ui` and `app.jobs` sit above `app.data` and `app.logic`.
   Lower layers never import upper ones; `ui` and `jobs` never import each
   other; `data` and `logic` never import each other.
2. **Pure logic.** `app.logic` imports none of `ctzd`, `dash`, `httpx`,
   `mssql_python`, `oracledb`, `streamlit`.
3. **Pages use loaders.** `app.ui` imports none of `httpx`, `mssql_python`, `oracledb`.
4. **Loaders have no UI.** `app.data` imports neither `dash` nor `streamlit`.

A failure reads `app.logic is not allowed to import ctzd: app.logic.prices -> ctzd (l.1)`.
Move the code to the layer it belongs in; never route around a contract.

## Splitting a request

- It reads something: a loader in `data/` (see `ctzd-data`).
- It computes, filters, ranks, joins, or decides: a function in `logic/`. Pages
  and callbacks hold no rules: tests cannot reach them there, and the coverage
  gate counts only `logic/`.
- It shows something or takes input: the page in `ui/`.
- It must happen on a schedule, or a query is slow: a job in `jobs/` (see `ctzd-jobs`).

## Worked example: "Today's bond prices by sector, with a date picker"

Source and table names here are examples: use the sources the SBU skill or the
person names. List the source in `app.toml` (`[sources]` `use = ["pricing_db"]`),
then run `ctzd env --json`.

`src/app/data/sql/prices.sql`

```sql
SELECT isin, sector, clean_price
FROM bond_prices
WHERE price_date = :as_of
```

`src/app/data/loaders.py`

```python
import datetime as dt

import ctzd
import pandas as pd


@ctzd.cached(ttl="15m")
def load_prices(as_of: dt.date) -> pd.DataFrame:
    frame = ctzd.query("pricing_db", "sql/prices.sql", {"as_of": as_of})
    return ctzd.require_columns(frame, {"isin": "str", "sector": "str", "clean_price": "float"}, what="prices")
```

`src/app/logic/prices.py`

```python
import pandas as pd


def sector_summary(prices: pd.DataFrame) -> pd.DataFrame:
    """One row per sector: bond count and average clean price, most bonds first."""
    summary = prices.groupby("sector", as_index=False).agg(bonds=("isin", "count"), avg_price=("clean_price", "mean"))
    return summary.sort_values(["bonds", "sector"], ascending=[False, True], ignore_index=True)
```

`src/app/ui/main.py` (Streamlit; the Dash version is in `ctzd-dash`)

```python
import datetime as dt

import streamlit as st

from app.data.loaders import load_prices
from app.logic.prices import sector_summary

st.set_page_config(page_title="Bond Prices", layout="wide")
st.title("Bond prices by sector")
as_of = st.sidebar.date_input("Price date", value=dt.datetime.now(dt.UTC).date())

prices = load_prices(as_of)
if prices.empty:
    st.info(f"No prices for {as_of:%d %b %Y}.")
else:
    summary = sector_summary(prices)
    st.bar_chart(summary, x="sector", y="bonds")
    st.dataframe(summary, hide_index=True)
```

Tests: `tests/test_prices.py` for `sector_summary` and `tests/test_ui.py` for
the page against fake sources (see `ctzd-testing`).

## Follow-up requests

- "Email me the sector summary every weekday at 7": a job
  `src/app/jobs/sector_summary.py` that reuses `load_prices` and
  `sector_summary`, writes the table, and sends the email. No new logic.
- "The page takes 20 seconds": the job writes `sector_summary` with
  `ctzd.write_table`; a new loader `load_sector_summary()` in `data/` calls
  `ctzd.read_table("sector_summary")`; the page calls that loader instead.
- "Add each bond's rating": a REST loader `load_ratings()` in `data/`, a logic
  function `with_rating(prices, ratings)` that merges them, and one more page
  column.
