---
name: python-standards
description: "House Python rules for ctzd apps: typing, data checked where it enters, pandas merges, errors, async only in callbacks, logging, dependencies, and fixing ruff and ty findings without suppressions. Use whenever you write, change or review Python in the app, and when the lint or types step of ctzd check fails."
---

# Python standards

Python 3.14 and pandas 3. ctzd owns the tool settings: ruff with every rule family, ty with every rule as an error,
import-linter for the layers. `ctzd check` runs them all. Fix the code they flag. Never add `# noqa`,
`# type: ignore`, `# ty: ignore`, per-file ignores or exclusions, and never edit `ruff.toml`, `ty.toml`,
`.importlinter` or `pytest.toml`: they are locked, and ctzd puts them back.

## Types

- Annotate every function, tests included: each parameter and the return (`-> None` too). A page's layout returns
  its outer component (`-> dmc.Stack`); a callback with several outputs returns `tuple[object, ...]`.
- Never write `Any`. An unknown payload, such as `json.loads`'s result, is `object`: narrow it with `isinstance`.
- Use `None` only when absence is a real state, and test it with `is not None`, never truthiness: `if count:` treats
  0 as missing. A filter a person may clear arrives as `str | None`.
- Make boolean parameters keyword-only: `def positions(holdings: pd.DataFrame, *, include_closed: bool = False)`.
  A choice among a few values is a `Literal["daily", "weekly"]`.
- Write a docstring or comment only for what the code cannot say: units, intent, how an outside system behaves.

## Data and errors

- Rates, spreads and weights are fractions (0.0525), every number a float64. Never round, in any layer.
- Use timezone-aware times: `datetime.now(ZoneInfo("Europe/London"))`, never a naive `now()` or `date.today()`.
  Pass "today" into functions as a parameter; only a layout default or a job's `main()` reads the clock.
- Never catch an exception to log it and return a default or an empty frame: that turns an outage into wrong
  numbers. Let it raise: on a page, `ui.on_error` tells the person; in a job, Airflow retries and emails the owners.
  No bare `except:`, no `except Exception: pass`.
- SQL lives in `.sql` files with bind parameters, never built from strings.
- No `print` in app code; log with `_LOG = logging.getLogger(__name__)` and `%s` arguments:
  `_LOG.info("loaded %d rows", len(prices))`.
- `async` belongs only to a page's callbacks, which `await ui.load(...)`. `data/`, `logic/` and jobs are plain
  synchronous functions.
- Module level holds constants only: no reads, no clock, no network when a module is imported.

## pandas

- Pass `validate=` to every `merge` and `join`. Without it a duplicated key silently multiplies rows.
- Never rely on index alignment between differently indexed frames or series: merge on an explicit key.
- Copy-on-write: no chained assignment and no `inplace=True`; use `.loc[mask, "col"] = ...` or `.assign(...)`.
- Name frames for what they hold (`prices`, `sectors`), not `df`.
- Use `.to_numpy()`, not `.values`; `.isna()`, not `== None`.

## Fixing common findings

| Finding | Fix |
|---|---|
| `ANN001`, `ANN201`: missing annotation | Add the type. |
| `PLR2004`: magic value | Name it as a module constant: `CHEAP_PRICE = 95.0`. |
| `FBT001`, `FBT002`: boolean positional argument | Put `*,` before it. |
| `EM101`, `EM102`, `TRY003`: message inside `raise` | `msg = f"..."`, then `raise ValueError(msg)`. |
| `DTZ005`, `DTZ011`: naive datetime | `datetime.now(ZONE)` with a `ZoneInfo`. |
| `T201`: `print` | `logging`. |
| `G004`: f-string in a log call | `%s` arguments. |
| `ERA001`: commented-out code | Delete it. |
| `S608`: SQL built from strings | A `.sql` file with bind parameters. |
| `INP001`: folder is not a package | Add an empty `__init__.py`. |
| `PD901`: a frame named `df` | A name for what it holds. |
| `C901`, `PLR0912`, `PLR0913`: too complex, too many arguments | Split the function; move rules into small `logic/` functions. |
| ty `invalid-return-type` or `invalid-assignment` from a pandas value | Build the value under a declared type: `total = float(frame["market_value"].sum())`. |
| ty `unresolved-attribute` | The object has no such attribute: check its type. Never reach for `getattr`. |

ruff fixes some findings itself during `ctzd check` (`"state": "fixed"`): read what changed.

## Dependencies

- Add a package with `uv add PACKAGE`; it comes from the firm's internal index. Prefer what is installed: pandas,
  numpy, Dash, dash-mantine-components, dash-ag-grid, Plotly, and the department's own packages the kit brings.
- Never edit `uv.lock`, the `[build-system]`, `[dependency-groups]` or `[tool.uv]` tables, or `requires-python`, and
  never add a database driver, an HTTP client for a source, or a second kit: data comes through the kit.
