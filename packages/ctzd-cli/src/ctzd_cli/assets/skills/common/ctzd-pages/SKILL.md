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

const formats = { avg_price: { decimals: 3 }, bonds: { decimals: 0 } };

function PricesPage(): ReactElement {
  const dataset = useDataset("sector_prices", prices);
  return (
    <div className="flex h-96 flex-col">
      <Grid dataset={dataset} layout={{ formats }} />
    </div>
  );
}
```

- `useDataset(name, load)` loads the dataset once into parallax's engine, which runs off the page.
- `<Grid>` and `<Chart>` fill their container, which must be a flex column with a height:
  `<div className="flex h-96 flex-col">`, and a chart also takes `className="min-h-0 flex-1"`;
  without them a chart draws only a sliver. The grid sorts, filters, groups, totals, and exports
  to Excel by itself.
- A bar, pie, or donut chart draws groups: `group_by` names the column of categories and
  `measures` what to draw for each,
  `<Chart dataset={dataset} layout={{ chart: { kind: "bar" }, group_by: ["sector"], measures: [{ aggregate: "mean", column: "avg_price" }], formats }} />`.
  `aggregate` is `sum`, `mean`, `min`, `max`, or `count`; for one row per category, `sum` draws
  each row's value.
- A line, area, or scatter chart draws columns against a column:
  `<Chart dataset={dataset} layout={{ chart: { kind: "line", x: "as_of", y: ["yield"], series: "tenor" }, formats }} />`.
  Other kinds: `heatmap`, `histogram`, `candlestick`, `ohlc`. A chart that shows "No groups to
  show" is a bar or pie chart without `group_by` and `measures`.
- A small chart of rows the page already holds: `<Chart rows={rows} kind="bar" x="sector" y="exposure" />`.
- `formats` writes each column's numbers: `{ decimals: 2 }`, `{ percent: true }` and
  `{ bps: true }` (both for fractions: 0.0525 is 5.25% and 525 bp), `{ thirty_seconds: "halves" }`
  for Treasury prices, `{ abbreviate: "millions" }`. Until the formats come from the dataset's row
  types by themselves, give each number column the format its row type names.
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

They keep shadcn's names and props: `Button`, `Card` with `CardHeader`, `CardTitle` and
`CardContent`, `Tabs`, `Badge`, `Alert`, `Select`, `Input`, `Skeleton`, `ErrorAlert`, and
`TypographyH1`, `TypographyLead` for text. A number in text goes through a number component,
which rounds it as the grid does: `<Num value={total} decimals={0} />`, `<Pct value={share} />`,
`<Bps value={spread} />`, `<Money value={amount} currency="USD" />`, `<Price32 value={price} />`.

Rules, which `ctzd check` enforces with the fix in each message:

- Never restyle a component: no `className` on it beyond placement, no colours outside the
  theme's tokens (`bg-muted`, `text-muted-foreground`), no `style`, no arbitrary values such as
  `p-[13px]`, no `dark:` classes. When nothing fits, tell the person the look is the firm's.
- Never round or format a number yourself (`toFixed`, `Math.round`, `toLocaleString`,
  `Intl.NumberFormat`): use the number components or parallax's `formats`.
- Never call `fetch`: data comes only through `#api/client.ts`.
- Under each page's title, say where its numbers come from and the date they are for.

## Add a page

1. Add its dataset to `src/app/api/datasets.py` (see `ctzd-data`).
2. Write `web/src/pages/NAME.tsx`, exporting `NamePage`.
3. List it in `web/src/routes.ts`: `createRoute({ component: NamePage, getParentRoute: () => rootRoute, path: "/name" })`,
   and add the route to `rootRoute.addChildren([...])`.
4. Run `ctzd dev`, then `ctzd shot /name --json`. Look at the picture it names in `picture`, and
   fix each line in `problems` before you give the person the link.

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

4. A page with a bar chart grouped by `sector` with the `mean` of `avg_price` above a grid of the rows, as above.

## Look at every page you change

After each change, run `ctzd dev` (it rebuilds the page) and `ctzd shot PATH --json`. It waits
for parallax to draw, writes `.ctzd/shots/NAME.png`, and lists `problems`: errors the page logs or
throws, requests that fail, a grid or chart that drew nothing ("No groups to show"), and a chart
drawn too small. Open the picture and check what the person asked for: the right columns, the
bars in a sensible order, labels that read. `ok: true` with a picture that looks wrong is still
wrong.

## When a check fails

- `web check`: each problem names the file, the rule, and what to use instead.
- `web build` mentioning the React Compiler: a component breaks the rules of React, such as a
  hook inside a condition or a value changed while rendering. Move hooks to the top of the
  component and compute new values instead of changing old ones.
- `client`: a dataset does not import, or its row type is wrong; the message names it.
