/* The start page: notional traded per desk as a bar chart, and every trade in the grid. */

import { Chart, Grid, useDataset } from "@basis/parallax";
import { TypographyH1 } from "@basis/ui";
import type { ReactElement } from "react";

import { deskNotional, trades } from "#api/client.ts";

/** Each dataset as the API answers it, an Arrow stream. */
const desks = async (): Promise<ArrayBuffer> => {
  const answer = await deskNotional();
  return answer.data.arrayBuffer();
};

const tradeRows = async (): Promise<ArrayBuffer> => {
  const answer = await trades();
  return answer.data.arrayBuffer();
};

function HomePage(): ReactElement {
  const byDesk = useDataset("desk_notional", desks);
  const allTrades = useDataset("trades", tradeRows);
  return (
    <>
      <TypographyH1>Desk Trades</TypographyH1>
      <div className="h-80">
        <Chart dataset={byDesk} layout={{ chart: { kind: "bar", x: "desk", y: ["notional"] } }} />
      </div>
      <div className="h-96">
        <Grid dataset={allTrades} layout={{}} />
      </div>
    </>
  );
}

export { HomePage };
