---
name: ctzd-pages
description: "Build and change the pages of a ctzd app: React pages in web/src/pages on the firm's components (@basis/ui) and parallax's grid, pivot and charts (@basis/parallax), fed by the app's datasets through the generated client. Use for any request to show, chart, filter, lay out, or change how something looks on the dashboard."
---

# Pages

A page is a React component in `web/src/pages/`, listed in `web/src/routes.ts`. It shows
datasets (see `ctzd-data`) with the firm's components and parallax. The components already
carry the firm's look in light and dark; a page only arranges them.

## How a page gets its data

A page never fetches. Each dataset in `src/app/api/datasets.py` becomes a function in the
generated client, which `ctzd dev` and `ctzd check` regenerate whenever the datasets change.
Import from `#api/client.ts`, never from `#api/generated/`:

| Dataset in Python | In the page |
|---|---|
| `def category_shares() -> pd.DataFrame` | `categoryShares()`, which answers the rows as Arrow: `(await categoryShares()).data` is a Blob |
| `def sector_prices(as_of: dt.date \| None = None)` | `sectorPrices({ as_of: "2026-11-06" })`: the parameters as one object |

## Grids and charts: parallax

```tsx
import { Chart, Grid, useDataset } from "@basis/parallax";

import { sectorPrices } from "#api/client.ts";

const prices = async (): Promise<ArrayBuffer> => {
  const answer = await sectorPrices();
  return answer.data.arrayBuffer();
};

function PricesPage(): ReactElement {
  const dataset = useDataset("sector_prices", prices);
  return (
    <div className="h-96">
      <Grid dataset={dataset} layout={{}} asOf="2026-11-06" />
    </div>
  );
}
```

- `useDataset(name, load)` loads the dataset once into parallax's engine, which runs off the page.
- `<Grid>` and `<Chart>` fill their container, so give it a height: `<div className="h-96">`.
  The grid sorts, filters, groups, totals, and exports to Excel by itself.
- A chart draws columns against a column:
  `<Chart dataset={dataset} layout={{ chart: { kind: "bar", x: "sector", y: ["avg_price"] } }} />`.
  `kind` is `line`, `area`, `bar`, `scatter`, `heatmap`, `histogram`, `candlestick`, `ohlc`,
  `pie`, or `donut`; `series` splits a column into coloured series. To total many rows per
  category instead, give `group_by: ["sector"]` and `measures: [{ aggregate: "sum", column: "par" }]`
  (`aggregate` is `sum`, `mean`, `min`, `max`, or `count`).
- A small chart of rows the page already holds: `<Chart rows={rows} kind="bar" x="sector" y="exposure" />`.
- Each number column shows by its row type's kind (`ctzd.Price`, `ctzd.Pct`, ...): the grid,
  the chart, and its tooltips read it from the dataset, so a page names no formats. Change a
  column's kind in `src/app/api/datasets.py`, not in the page. A layout's `formats` overrides
  one column only when a page must show it differently from everywhere else.
- Give every `<Grid>` and `<Pivot>` the date its data is for as `asOf`, an ISO date
  (`"2026-11-06"`): its status bar then says "as of 2026-11-06". Use the day the page asked the
  dataset for, or, for "the latest", the newest date in the data, from a one-row dataset such as
  `latest_date` that the page reads with its generated function. Never the day the page loaded:
  without `asOf` the bar says no date at all, and a malformed one shows an error in its place.
- `ParallaxProvider` in `web/src/pages/layout.tsx` starts the engine for every page; leave it there.

Pick the chart from the person's question:

| They ask | Chart |
|---|---|
| How something changed over time | `line`, `x` a date column |
| How categories compare | `bar` |
| What share each part has | `bar` of the shares; `pie` only if they ask |
| How one measure sits against another, such as spread against duration | `scatter`, `series` to colour by a column |
| How values spread out | `histogram` |
| Prices with open, high, low and close | `candlestick` |

## The firm's components: @basis/ui

`ctzd docs ui --search WORDS --json` finds components by what they are for ("date picker"),
`ctzd docs ui --json` lists every component by family, and `ctzd docs ui NAME --json` gives one
component's use, what goes with it, props, variants, and a working example, for the version the
app installed: read it before you use a component rather than guessing its props. Never open
`node_modules/@basis/ui/catalogue.json` itself: it is about 1 MB. They keep shadcn's names and props:
`Button`, `Card` with `CardHeader`, `CardTitle` and
`CardContent`, `Tabs`, `Badge`, `Alert`, `Select`, `Input`, `Skeleton`, `ErrorAlert`, and
`TypographyH1`, `TypographyLead` for text. A number in text goes through a number component,
which rounds it as the grid does: `<Num value={total} decimals={0} />`, `<Pct value={share} />`,
`<Bps value={spread} />`, `<Money value={amount} currency="USD" />`, `<Price32 value={price} />`.

Rules, which `ctzd check` enforces with the fix in each message:

- Never restyle a component: no `className` on it beyond placement, no colours outside the
  theme's tokens (`bg-muted`, `text-muted-foreground`), no `style`, no arbitrary values such as
  `p-[13px]`, no `dark:` classes. When nothing fits, tell the person the look is the firm's.
- Never round or format a number yourself (`toFixed`, `Math.round`, `toLocaleString`,
  `Intl.NumberFormat`): use the number components or parallax's `formats`, and
  `numberText(value, { decimals })` from `@basis/ui` for a number inside a string.
- Never call `fetch`: data comes only through `#api/client.ts`.
- Under each page's title, say where its numbers come from, in the person's words ("the
  pricing team's end-of-day prices"), and the date they are for, taken from the data (the
  newest `as_of_date` in the rows), never the day the page loaded. The people who open a
  dashboard are not only the person who built it.
- The footer in `web/src/pages/layout.tsx` names the app's owner and says the figures are best
  effort. Keep it.
- `web/src/pages/home.tsx` starts as the starter's sample page, marked by its first comment line.
  Replace it with the person's own page and delete that line: until then `ctzd deploy` refuses
  to ship the app, and Jenkins deploys nothing.

## Add a dataset and a page

Two commands write a working slice, which passes `ctzd check` as written; then you edit it:

1. `ctzd add dataset NAME --source SOURCE --json` writes `src/app/data/sql/NAME.sql`, a loader,
   the dataset and its row type in `src/app/api/datasets.py`, and a test, lists the source in
   `app.toml`, and regenerates the client. Without `--source`, the loader returns example rows.
   Write the query, then make the row type's fields the query's columns (`ctzd-data`).
2. `ctzd add page PAGE --dataset NAME --json` writes `web/src/pages/PAGE.tsx`, showing the
   dataset as a bar chart and a grid routes it at
   `/PAGE`, and links it from the header (`web/src/nav.ts`). Change the chart's columns and kind
   to answer the person's question.
3. Run `ctzd dev`, then `ctzd shot /PAGE --json`. Look at the picture it names in `picture`, and
   fix each line in `problems` before you give the person the link.

`ctzd api list --json` names every dataset's route, client function, parameters, and columns
with their formats: read it instead of the generated client.

## A worked example: "today's bond prices by sector"

1. `src/app/data/sql/prices.sql` and `load_prices(as_of)` in `src/app/data/loaders.py` (`ctzd-data`).
2. `sector_summary(prices) -> pd.DataFrame` in `src/app/logic/`: bonds and average price per sector.
3. The dataset, with a row type that says how the page shows each column:

   ```python
   class SectorPrice(ctzd.Row):
       sector: str
       bonds: ctzd.Count
       avg_price: ctzd.Price


   @ctzd.dataset(SectorPrice)
   def sector_prices(as_of: dt.date | None = None) -> pd.DataFrame:
       """Bonds and average clean price per sector."""
       return sector_summary(load_prices(as_of))
   ```

4. A page with a bar chart of `avg_price` by `sector` above a grid of the rows, as above.

## Look at every page you change

After each change, run `ctzd dev` (it rebuilds the page) and `ctzd shot PATH --json`. It waits
for parallax to draw, writes `.ctzd/shots/NAME.png`, and lists `problems`: errors the page logs or
throws, requests that fail, a grid or chart that drew nothing ("No groups to show"), and a chart
drawn too small. Open the picture and check what the person asked for: the right columns, the
bars in a sensible order, labels that read. `ok: true` with a picture that looks wrong is still
wrong.

## When a check fails

- `web check`: each problem names the file, the rule, and what to use instead. A restyled
  component names its variants and `vp run docs NAME`, and `ctzd docs ui NAME --json` gives the
  same; an off-theme colour names the theme's tokens to use.
- `web build` mentioning the React Compiler: a component breaks the rules of React, such as a
  hook inside a condition or a value changed while rendering. Move hooks to the top of the
  component and compute new values instead of changing old ones.
- `client`: a dataset does not import, or its row type is wrong; the message names it.
