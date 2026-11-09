---
name: python-standards
description: "House Python rules for ctzd apps: typing, validating data where it enters, pandas merges, error handling, network timeouts, and fixing ruff and ty findings without suppressions. Use whenever you write, change, or review Python in the app, and when the lint or types step of ctzd check fails."
---

# Python standards

ctzd owns the tool settings: ruff with every rule, ty with every rule as an
error, import-linter for the layers. `ctzd check` runs them all. Fix the code
they flag. Never add `# noqa`, `# type: ignore`, `# ty: ignore`, per-file
ignores, or exclusions, and never edit `ruff.toml`, `ty.toml`, `.importlinter`,
or `pytest.toml`: they are platform-owned, and `ctzd check` puts them back.

## Types

- Annotate every function, tests included: each parameter and the return
  (`-> None` too).
- Never write `Any`. An unknown payload, such as `get_json`'s result or
  `json.loads`, is `object`: narrow it with `isinstance` before use.
- Use `None` only when absence is a real state, and test it with `is not None`,
  never truthiness: `if count:` treats 0 as missing.
- Make boolean parameters keyword-only:
  `def summarize(prices: pd.DataFrame, *, include_total: bool = False)`.
  A choice among a few values is a `Literal["daily", "weekly"]`.
- Model a value with several shapes as a union of frozen dataclasses, and
  `match` on it with `assert_never` in the last case.
- Write a docstring or comment only for what the code cannot say: units,
  intent, how an outside system behaves. Never restate the name.

## Data at the edges

- Check data where it enters, in the loader: `ctzd.require_columns` for frames,
  `isinstance` for JSON. Logic may then trust what it receives.
- Use timezone-aware times: `dt.datetime.now(dt.UTC)`, never naive `now()` or
  `date.today()`. Pass "today" into functions as a parameter.
- Never catch an exception to log it and return a default or an empty frame:
  that turns an outage into wrong numbers. Let it raise, or re-raise with
  context (`raise ctzd.DataError(msg) from err`). No bare `except:`, no
  `except Exception: pass`.
- Every network call has a timeout. `ctzd.query` and `ctzd.rest` set one; use
  them rather than another client. If an added package calls the network, pass
  its timeout argument.
- SQL lives in `.sql` files with bind parameters, never built from strings.
- No `print` in app code; log with `_LOG = logging.getLogger(__name__)` and
  `%s` arguments: `_LOG.info("loaded %d rows", len(prices))`.

## pandas

- Pass `validate=` to every `merge` and `join`. Without it a duplicated key
  silently multiplies rows.
- Never rely on index alignment between differently indexed frames or series:
  arithmetic between them gives `NaN`. Merge on an explicit key.
- Name frames for what they hold (`prices`, `summary`), not `df`.
- Use `.to_numpy()`, not `.values`; `.isna()`, not `== None`.

Added to `src/app/logic/prices.py` from `ctzd-layout`:

```python
def with_rating(prices: pd.DataFrame, ratings: pd.DataFrame) -> pd.DataFrame:
    """Each price with its bond's rating; a bond with no rating gets NaN."""
    return prices.merge(ratings[["isin", "rating"]], on="isin", how="left", validate="many_to_one")
```

## Fixing common findings

| Finding | Fix |
|---|---|
| `ANN001`, `ANN201`: missing annotation | Add the type. |
| `PLR2004`: magic value | Name it as a module constant: `CHEAP_PRICE = 95.0`. |
| `FBT001`, `FBT002`: boolean positional argument | Put `*,` before it. |
| `EM101`, `EM102`, `TRY003`: message inside `raise` | `msg = f"..."`, then `raise ValueError(msg)`. |
| `DTZ005`, `DTZ011`: naive datetime | `dt.datetime.now(dt.UTC)`. |
| `T201`: `print` | `logging`. |
| `G004`: f-string in a log call | `%s` arguments. |
| `ERA001`: commented-out code | Delete it. |
| `S608`: SQL built from strings | A `.sql` file with bind parameters. |
| `INP001`: folder is not a package | Add an empty `__init__.py`. |
| `PD901`: variable named `df` | A descriptive name. |
| `C901`, `PLR0912`, `PLR0913`: too complex, too many arguments | Split the function; move rules into small logic functions. |
| ty `unsound-return-statement`, `invalid-assignment` with `Any` from pandas | Build the value under a declared type with a comprehension: `prices: list[float] = [float(price) for price in frame["price"]]`. |
| ty `unsound-return-statement` on `frame[frame["d"].dt.date == day]` | The `.dt` accessor is untyped; compare the column itself: `frame.loc[frame["d"] == pd.Timestamp(day)]`. |
| ty `unresolved-attribute` | The object has no such attribute: check its type. Never reach for `getattr`. |

ruff fixes some findings itself during `ctzd check` (`"state": "fixed"`): read
what changed.

## Dependencies

- Add a package with `uv add PACKAGE` (it comes from the internal index). Prefer
  what is installed: pandas, numpy, and the UI framework's charts.
- Never edit `uv.lock`, the `[build-system]`, `[dependency-groups]`, or
  `[tool.uv]` tables, or `requires-python`.
