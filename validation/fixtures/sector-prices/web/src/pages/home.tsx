/* The start page: each sector's average clean price as a bar chart, and the same rows in the grid. */

import { Chart, Grid, useDataset } from "@basis/parallax";
import { TypographyH1 } from "@basis/ui";
import type { ReactElement } from "react";

import { sectorPrices } from "#api/client.ts";

/** The dataset as the API answers it, an Arrow stream. */
const rows = async (): Promise<ArrayBuffer> => {
  const answer = await sectorPrices();
  return answer.data.arrayBuffer();
};

function HomePage(): ReactElement {
  const dataset = useDataset("sector_prices", rows);
  return (
    <>
      <TypographyH1>Sector Prices</TypographyH1>
      <div className="h-80">
        <Chart
          dataset={dataset}
          layout={{ chart: { kind: "bar", x: "sector", y: ["clean_price"] } }}
        />
      </div>
      <div className="h-80">
        <Grid dataset={dataset} layout={{}} />
      </div>
    </>
  );
}

export { HomePage };
