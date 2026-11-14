---
name: ctzd-kits
description: "Build and maintain a department's ctzd kit in this repository: ctzd kit new, ctzd kit add source NAME --kind KIND, each kind's loader (oracle and mssql with query, rest with rest(), file with files.read, package wrapping a department library with io and [secret.env] or [secret.file]), sample rows, NOTES.md and the generated sbu-<sbu> skill, pure helpers, the seven contract items and ctzd kit check, releases and major versions. Use whenever you work in kits/<sbu>, or a department asks for a source, a dataset or a package to reach its apps."
---

# Kits

A kit is how a department brings its data and its own packages to ctzd: a Python package, `ctzd-<sbu>` (module
`ctzd_<sbu>`), in `kits/<sbu>/` of this repository, maintained by the department's engineers. Every app depends on
exactly one kit, and reads data only through it. A kit builds on `ctzd-kit` (`packages/ctzd-kit`), which holds the
loader seam, the fakes, the store, the drivers and the source explorer; only kits import `ctzd_kit`.

The platform owns one seam and the delivery of secrets; everything behind the seam is the department's.

- **The seam is the loader.** An app reads by calling a kit loader, or by running its own SQL through the kit's
  `query`. With fakes on, a loader returns its sample rows and never runs what is underneath, which is what lets
  `ctzd check` run with no credentials and no network.
- **Secrets come from Key Vault**, delivered in whatever form the code underneath expects.

This repository is public: `kits/fi` and `kits/mas` are made-up departments, the worked examples. Hosts,
organisations and people are `contoso` placeholders. A real kit names real sources and tables, so real kits are
committed only to the firm's fork, never here.

## What a kit looks like

```text
kits/fi/
├── pyproject.toml              # ctzd-fi: depends on ctzd-kit and on FI's own packages; its ctzd.kits entry point
├── src/ctzd_fi/
│   ├── __init__.py             # what apps import; `ctzd kit add source` rewrites it
│   ├── kit.toml                # the department: sbu, title, GitHub team, code owners
│   ├── NOTES.md                # the department's conventions, in its own words
│   ├── helpers/                # pure functions apps may call in logic/
│   └── sources/
│       ├── fi_pricing/         # one folder per source
│       │   ├── source.toml     # what it holds, when it is current, its kind, how its secret arrives
│       │   ├── loaders.py      # the functions apps call
│       │   ├── sql/            # the loaders' SQL, for a database
│       │   └── fixtures/       # sample rows: one LOADER.csv per loader
│       └── fi_risk/
└── tests/
```

Apps import `from ctzd_fi import fi_pricing, query, store, sample` and call `fi_pricing.prices(as_of)`. Every kit
also has `sample`, from `ctzd_kit`, made-up rows the app templates read.

## Starting a kit

```text
ctzd kit new SBU --title "Department name" --dir kits
```

It writes `kits/SBU/` with `kit.toml`, `NOTES.md`, `helpers/`, an empty `sources/`, a contract test, and lists the
kit in the repository's workspace. Then:

1. Fill `kit.toml`: `team` is the GitHub team whose members may create apps on the kit, `owners` its code owners.
2. List the department in `packages/ctzd-cli/src/ctzd_cli/platform.toml` as `[sbus.SBU]`, with `name`, `kit`
   (`"ctzd-SBU>=0.1,<1"`) and `github_team`, and run `uv sync`. A new department needs a CLI release (raise
   `version` in `packages/ctzd-cli/pyproject.toml`), because the CLI carries the list of departments. A new source
   does not.
3. Write `NOTES.md`, then add the first source.

## Adding a source

```text
ctzd kit add source NAME --kind oracle|mssql|rest|file|package
```

Run it in the kit's folder (or pass `--dir kits/SBU`). NAME is lowercase letters, digits and underscores, prefixed
with the department (`fi_pricing`), and never `sample`. It writes `sources/NAME/` with a working loader for that
kind, its `source.toml`, sample rows in `fixtures/example.csv` (and `sql/example.sql` for a database), a test in
`tests/test_NAME.py`, and rewrites the kit's `__init__.py`. Then:

1. Say in `source.toml` what the source holds and when it is current, in business words: apps' agents choose a
   source by its description.
2. Rename `example` to each real read, with a docstring saying what it returns in the department's words, and its
   sample rows to `fixtures/LOADER.csv`. Update the test.
3. `ctzd kit check` (below).
4. A pull request, approved by the kit's code owner. CI releases the kit under its own version when it merges.
5. Ask the platform team to add the source's secret to the prod Key Vault, and to the dev one if the department
   wants real data in dev. Without a dev secret, dev serves the sample rows, and pages show the sample-data banner.
6. Apps get the source the next time someone works on them, through `ctzd sync`.

### `source.toml`

```toml
description = "Bond prices the pricing team publishes each morning: clean price, yield to worst and spread"
refreshed = "each business day by 07:00 London"
kind = "oracle"                 # oracle | mssql | rest | file | package
max_concurrent = 4              # reads at once per app process, jobs included (1 to 32, default 4)
desktop = true                  # false only when it cannot be read from a desktop: then it always serves sample rows there
```

A `package` source adds `io` and `[secret]` (below). `desktop = false` is a fallback to fix, and `ctzd kit check`
warns about every source that sets it.

## Each kind's loader

Every loader is a plain synchronous function decorated with `@loader`, in `sources/NAME/loaders.py`, returning a
DataFrame. The seam then makes every number a float64 and refuses an id (`id` or `..._id`) that holds numbers.

**`oracle`, `mssql`**: a database, through SQL files in `sql/`. Binds by name: `:name` for Oracle, `%(name)s` for SQL
Server. Apps may also write their own SQL against the source, and `ctzd sources NAME` lists its tables.

```python
from datetime import date

import pandas as pd

from ctzd_kit import loader, query


@loader
def prices(as_of: date) -> pd.DataFrame:
    """Every bond's published price on `as_of`: its ISIN, issuer and sector, its clean price per 100 of par, its yield
    to worst and its option-adjusted spread, both as fractions."""
    return query("prices.sql", as_of=as_of)
```

```sql
-- sql/prices.sql: ids as text, every number as stored, nothing rounded
select isin, issuer, sector, clean_price, ytw, oas, as_of
from fi_prices.bond_prices
where as_of = :as_of
```

**`rest`**: an API, through `rest()`, the source's client with its auth, timeouts, three attempts on 429 and 5xx,
and paging. Call it inside the loader, never at module level.

```python
from ctzd_kit.drivers.rest import rest


@loader
def treasury_curve(as_of: date) -> pd.DataFrame:
    """The Treasury curve marked on `as_of`: each tenor, its years, and its yield as a fraction."""
    return rest().get_frame("/curves/treasury", {"as_of": as_of.isoformat()}, records="points")
```

`get_frame(path, params, records=KEY)` reads one response's list of rows; `get_all_pages(path, params, records=KEY,
next_key="next")` follows the next link until it is empty; `get_json` and `post_json` return the raw JSON as
`object`, to narrow with `isinstance`.

**`file`**: CSV, Excel or Parquet files in a folder or a blob container, through `files.read(PATH)`, a path inside
the source's folder; `files.listed(FOLDER)` lists the files under one, in name order.

```python
from ctzd_kit.drivers import files


@loader
def allocations(as_of: date) -> pd.DataFrame:
    """Each portfolio's weight in each asset class at the close of `as_of`, and its target, as fractions."""
    return files.read(f"allocations/{as_of.isoformat()}.xlsx")
```

**`package`**: a department library that makes its own connections. Add it to the kit's dependencies, list it in
`io` (apps may then never import it; only this source's loaders do), and say how its secret arrives. Nothing inside
the library changes.

```toml
kind = "package"
io = ["fi_risklib"]

[secret.env]                    # each variable the library already reads, and the secret's field it holds
FI_RISK_USER = "user"
FI_RISK_PASSWORD = "password"

# or, for a library that reads a file whose path is in a variable:
# [secret.file]
# variable = "FI_RISK_CONFIG"
# filename = "secret.json"      # the whole secret is written to this file
```

```python
import fi_risklib

from ctzd_kit import loader


@loader
def measures(as_of: date) -> pd.DataFrame:
    """DV01 and spread duration of every position on `as_of`, by book and ISIN. DV01 is in US dollars per basis point."""
    return fi_risklib.measures(as_of)
```

A loader's frame is the app's: numbers as float64, ids as text, columns named as the department names them. What
the loader does inside is its own business: convert a library's result there, never in the app.

### Secrets, for the platform team

A driver's source gets its whole secret, one JSON object, as `CTZD_SOURCE_<NAME>`, and declares no `[secret]`:

| Kind | The secret's fields |
|---|---|
| `oracle` | `kind`, `dsn` (`host:port/service`), `user`, `password` |
| `mssql` | `kind`, `server`, `database`, `user`, `password`, optional `encrypt`, `trust_server_certificate` |
| `rest` | `kind`, `base_url`, `auth` (`type` `none`, `bearer`, `header`, `basic` or `client_credentials`, with its fields), optional `timeout_seconds` |
| `file` | `kind`, `url` (a path, or `abfs://container@account.dfs.core.windows.net/prefix`), optional `sas_token` |
| `package` | whatever fields `[secret.env]` and `[secret.file]` name |

The account behind each is read-only. In an app's pods only the sources in its `app.toml` arrive; on a desktop,
`ctzd env` reads them from the dev Key Vault.

### Caching

A loader whose source changes rarely may cache its real reads in the process, for a dashboard's users and a job
alike: put `@cached(ttl="15m")` (from `ctzd_kit`) under `@loader`. Its arguments must be hashable: strings, numbers,
dates and tuples.

## Sample rows

Every loader has `fixtures/LOADER.csv`: three to ten made-up rows with exactly the columns the real read returns.
They are the default fake everywhere, so an app that only calls loaders writes no fixtures. They ignore the
loader's arguments. Never copy real rows into them: they are committed and shipped. How a column is read: `id` and
`..._id` stay text, as does a number with a leading zero (an account, a SEDOL); an ISO date in every row becomes a
date; `true` and `false` become booleans; any other number a float64; an empty cell is missing.

## NOTES.md and the department's skill

Every app's agent reads the skill `sbu-<sbu>`. It is generated from the kit itself (each source's description, each
loader's signature, docstring, columns and an example call), with `NOTES.md` added, so it cannot drift from the code;
`ctzd sources` shows the same facts. So the docstrings and descriptions are the documentation: write them for an
agent that knows nothing of the department. `NOTES.md` says what the code cannot: what books, desks and identifiers
mean, units and conventions (fractions or percent, per 100 of par), which source to reach for first, and which helper
to use for what.

## Helpers

`helpers/` holds pure functions, frames and values in, a frame or a value out: day counts, a rebalance, a
calendar. Apps may import them in `logic/`. A helper never reads: it imports no `ctzd_kit`, no source and no `io`
package, and touches no network; anything that reads is a loader. A pure department library (analytics, pricing)
needs no wrapper: list it as a dependency and apps import it by its own name, with a typed wrapper in `helpers/` only
where the library has no types.

## Tests

```python
from datetime import date

from ctzd_kit import testing

from ctzd_fi import fi_pricing


def test_prices_answer_their_sample_rows_with_numbers_as_floats() -> None:
    with testing.fakes():
        prices = fi_pricing.prices(date(2026, 11, 13))
    assert prices["clean_price"].dtype == "float64"
```

`testing.fakes()` serves every source's sample rows for the block, `testing.store(tmp_path)` puts the store in a
folder, and the kit's tests run with the network blocked.

## The contract and `ctzd kit check`

1. **Shape.** The package is `ctzd-<sbu>`, builds on `ctzd-kit`, and passes this repository's lint, type and test
   rules (ruff with every rule, ty with every rule as an error; fix findings, add no ignores).
2. **Installs from wheels**, on the desktop's Windows and the image's Linux, on Python 3.14 with pandas 3, beside
   `ctzd-common[dash]`. Desktops have no compiler.
3. **Sources.** Every system the kit reads is a source folder with a description, and its secret arrives only the
   way `source.toml` says.
4. **Loaders and fakes.** Every read an app can make is a loader with sample rows.
5. **Network only inside loaders.** The check runs with the network blocked: a helper that quietly calls a service
   fails, and must become a loader.
6. **Frames.** A loader hands the app a DataFrame whose numbers are float64 and whose ids are text.
7. **Guidance.** Every source has a description, every loader a docstring, `NOTES.md` exists, and the department's
   skill renders.

```text
ctzd kit check kits/SBU [--no-apps]
```

It runs `shape`, `rules` (this repository's ruff, ty and pytest on the kit), `contract` (each loader called on its
sample rows with the network blocked, helpers imported, the skill rendered), `wheels` (Windows and Linux), and
`apps`: an app of each template made on the kit and `ctzd check`ed. `--no-apps` skips the last while you iterate.
It warns about each source with `desktop = false`. Then run the repository's checks in `AGENTS.md`, including
`scripts/e2e.sh`.

## Releases and versions

- Each kit has its own version (`[project].version` in its `pyproject.toml`) and is released to the internal index
  when its pull request merges. Raise it with every change apps should get.
- An app's `uv.lock` pins its kit, and CI installs exactly that. `ctzd sync` moves an app to the kit's newest
  release within its major version.
- **A new major version** is for a change that can break an app: a loader removed or renamed, an argument or a
  column changed or dropped, a meaning changed. An app takes it only once its own checks pass on it, and otherwise
  stays and says so. Prefer adding a loader beside the old one, and keep the major version when you can.
- A driver or helper that a second kit needs moves into `ctzd-kit`, approved by the platform's owners. So does a new
  kind of source (Snowflake, the data lake): added to `ctzd-kit` by the first department that needs it, in the same
  driver shape.
- A department package that cannot run on Python 3.14 and pandas 3 either publishes a table where it runs today, for
  a loader to read, or is called in its own interpreter by a loader that reads back a file (slow; suits jobs). One
  that works only on a Windows desktop needs the first.
