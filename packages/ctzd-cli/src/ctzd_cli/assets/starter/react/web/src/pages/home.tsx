/* ctzd: sample page. Replace it with the person's own page and delete this line; until then ctzd deploy refuses to ship the app. */
/* The start page: each category's value as a bar chart, and every row in the grid. */

import { Chart, Grid, useDataset } from "@basis/parallax";
import { TypographyH1 } from "@basis/ui";
import type { ReactElement } from "react";

import { categoryShares } from "#api/client.ts";

/** The dataset as the API answers it, an Arrow stream. */
const shares = async (): Promise<ArrayBuffer> => {
  const answer = await categoryShares();
  return answer.data.arrayBuffer();
};

function HomePage(): ReactElement {
  const dataset = useDataset("category_shares", shares);
  return (
    <>
      <TypographyH1>{{title}}</TypographyH1>
      <div className="h-80">
        <Chart dataset={dataset} layout={{ chart: { kind: "bar", x: "category", y: ["value"] } }} />
      </div>
      <div className="h-80">
        <Grid dataset={dataset} layout={{}} />
      </div>
    </>
  );
}

export { HomePage };
