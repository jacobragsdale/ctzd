MAS's conventions, which every MAS app follows:

- A portfolio is named by its id (P-BAL, P-GRW, P-INC), an instrument by its own (EQ-WORLD, FI-AGG, CASH-GBP),
  always as text.
- Weights are fractions of the portfolio: 0.523 is 52.3%. Quantities are units; prices are in the instrument's
  currency.
- The allocation reports cannot be read from a desktop; a page that uses them shows sample rows there and real ones in
  dev and prod.
- To see how far portfolios are from target, use `ctzd_mas.helpers.rebalance.drift`, which `logic/` may import.
