---
name: ctzd-streamlit
description: "Build and test Streamlit pages in a ctzd app: page structure, cached loaders, st.form and st.fragment to avoid full reruns, sidebars, multipage navigation, speed, and AppTest tests against ctzd.testing fakes. Use when the app's ui is streamlit."
---

# Streamlit in a ctzd app

The platform runs `streamlit run src/app/ui/main.py`; `ctzd dev` does the same
locally. Streamlit is 1.66 or later.

## Page rules

- `st.set_page_config(page_title=..., layout="wide")` is the first Streamlit call the
  script makes (function definitions may come before it).
- The whole script reruns on every click, so keep the page thin: read through
  cached loaders from `app.data`, compute with `app.logic`, then display. No
  queries, rules, or loops over rows in the page.
- Caching is `@ctzd.cached` on the loader. Do not add `st.cache_data`.
- Put filters in `st.sidebar`. Group several inputs in `st.form` so the page
  reruns once, on submit, not on every change.
- Wrap a section with its own inputs (a drill-down, a chart picker) in a
  function decorated `@st.fragment`: its widgets rerun only that function.
- No data for the chosen input is a normal answer: show `st.info(...)`. Let
  real errors raise: an error box fails the post-deploy check and the release
  is rolled back, which is better than a page showing wrong numbers.
- Use `width="stretch"` or `width="content"`; `use_container_width` is deprecated.
- Wide tables: `st.dataframe(frame, hide_index=True, column_config={...})`.
  Show at most a few thousand rows; aggregate, or offer the rest with
  `st.download_button("Download CSV", frame.to_csv(index=False), file_name="prices.csv", mime="text/csv")`.
- Charts: `st.bar_chart`, `st.line_chart`, `st.area_chart`, `st.scatter_chart`
  take `x=` and `y=` column names, plus `x_label=` and `y_label=` for the axis
  titles the person reads.
- `st.bar_chart` sorts the bars alphabetically: pass `sort=False` to keep the
  frame's order (ratings best first, sectors by size).
- These charts start the y-axis at zero, which flattens values far from it,
  such as prices near 100. Fit the axis with `st.vega_lite_chart`:

  ```python
  st.vega_lite_chart(history, {
      "mark": {"type": "line", "point": True},
      "encoding": {
          "x": {"field": "as_of_date", "type": "temporal", "title": "Date"},
          "y": {"field": "average_price", "type": "quantitative", "title": "Average clean price", "scale": {"zero": False}},
      },
  })
  ```

  Altair's typed API does not pass `ctzd check`; use the spec above.
- Look at the deploy screenshot: a flat line or bars in the wrong order are
  mistakes to fix before you report.
- Numbers: `st.metric(label, value)`, and `st.columns(3)` to place several side by side.

## A page

`src/app/ui/main.py`

```python
import datetime as dt

import pandas as pd
import streamlit as st

from app.data.loaders import load_prices
from app.logic.prices import sector_summary


@st.fragment
def bond_detail(prices: pd.DataFrame) -> None:
    """Reruns on its own when the person picks another bond."""
    isin = st.selectbox("Bond", prices["isin"].tolist())
    st.dataframe(prices[prices["isin"] == isin], hide_index=True)


st.set_page_config(page_title="Bond Prices", layout="wide")
st.title("Bond prices by sector")

with st.sidebar.form("filters"):
    as_of = st.date_input("Price date", value=dt.datetime.now(dt.UTC).date())
    st.form_submit_button("Show")

prices = load_prices(as_of)
if prices.empty:
    st.info(f"No prices for {as_of:%d %b %Y}.")
else:
    summary = sector_summary(prices)
    left, right = st.columns(2)
    left.metric("Bonds", len(prices))
    right.metric("Sectors", len(summary))
    st.bar_chart(summary, x="sector", y="bonds")
    st.dataframe(summary, hide_index=True, column_config={"avg_price": st.column_config.NumberColumn("Avg clean price", format="%.2f")})
    bond_detail(prices)
```

## Several pages

Keep `main.py` as the entry point and put each page in `src/app/ui/pages/`
(with an empty `__init__.py`). The menu stays in the sidebar, where the
post-deploy check finds and opens every page.

```python
import streamlit as st

st.set_page_config(page_title="Bond Prices", layout="wide")
page = st.navigation([st.Page("pages/overview.py", title="Overview", default=True), st.Page("pages/history.py", title="Price history")])
page.run()
```

Each page file is an ordinary script (title, inputs, display) without `set_page_config`.

## Speed

1. Every loader is `@ctzd.cached` with a `ttl` that matches how often the data changes.
2. The SQL selects only needed columns and aggregates in the database.
3. A query slower than about 3 seconds moves into a job that writes a table
   (`ctzd-data`); the page reads the table.
4. Forms and fragments stop unrelated reruns. `st.spinner("Loading prices...")`
   tells the person something is happening.

## Tests

`AppTest` runs the page script without a browser. Wrap it in
`ctzd.testing.fake_sources` so no real source is touched; every source the page
reads must be faked. Name page tests by behavior.

`tests/test_ui.py`

```python
from pathlib import Path

import ctzd
import pandas as pd
from streamlit.testing.v1 import AppTest

UI = Path(__file__).parents[1] / "src" / "app" / "ui"


def prices() -> pd.DataFrame:
    return pd.DataFrame({"isin": ["XS0000000001", "XS0000000002"], "sector": ["Banks", "Utilities"], "clean_price": [99.5, 101.25]})


def test_page_shows_one_row_per_sector() -> None:
    with ctzd.testing.fake_sources(pricing_db={"sql/prices.sql": prices()}):
        page = AppTest.from_file(str(UI / "main.py")).run(timeout=30)
        assert not page.exception
        assert page.dataframe[0].value["sector"].tolist() == ["Banks", "Utilities"]
        assert page.metric[0].value == "2"


def test_choosing_a_bond_shows_only_that_bond() -> None:
    with ctzd.testing.fake_sources(pricing_db={"sql/prices.sql": prices()}):
        page = AppTest.from_file(str(UI / "main.py")).run(timeout=30)
        page.selectbox[0].select("XS0000000002").run()
        assert page.dataframe[1].value["isin"].tolist() == ["XS0000000002"]


def test_a_day_without_prices_says_so() -> None:
    with ctzd.testing.fake_sources(pricing_db={"sql/prices.sql": prices().iloc[0:0]}):
        page = AppTest.from_file(str(UI / "main.py")).run(timeout=30)
        assert not page.exception
        assert "No prices" in page.info[0].value
```

Keep assertions inside the `with` block when they rerun the page. Widgets are
reached by type and order (`page.sidebar.date_input[0].set_value(day).run()`,
`page.button[0].click().run()`). With several pages, test each file in
`pages/` with `AppTest.from_file`, or call `page.switch_page("pages/history.py").run()`
from `main.py`.
