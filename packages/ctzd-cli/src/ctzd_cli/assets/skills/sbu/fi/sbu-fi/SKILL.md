---
name: sbu-fi
description: "Fixed Income (FI) apps: the ctzd-fi library and where shared FI loaders live, FI data sources and conventions, and a glossary of fixed-income terms (ISIN, clean and dirty price, yield, duration, spread). Use when building or changing an FI app or when the person uses bond or rates vocabulary."
---

# Fixed Income (FI)

This app belongs to the FI business unit (`sbu = "fi"` in `app.toml`). Its
dashboard is served at `https://fi-NAME.ctzd.contoso.com`.

## The `ctzd-fi` library

- Every FI app depends on `ctzd-fi` (already in `pyproject.toml`). It declares
  the FI package stack and holds loaders that several FI apps share. It
  depends on `ctzd-core`, so `import ctzd` works as usual.
- The FI champion owns it, in the platform repository at
  `kits/fi/src/ctzd_fi/`. You cannot change it from an app. When a loader
  would help other FI apps, write it in this app's `src/app/data/` and tell the
  person it could be offered to the FI champion for `ctzd-fi`.
- Call a `ctzd_fi` loader only from `src/app/data/`, cached as usual, and
  treat what it returns like any other loader's output.
- To get a newer `ctzd-fi`: `uv add "ctzd-fi>=VERSION"`.

TODO (FI champion): list the shared loaders here: import path, arguments,
returned columns and units, and refresh times.

## Sources

TODO (FI champion): for each FI source: its name in `[sources].use`, kind
(`oracle`, `mssql`, or `rest`), what it holds, the main tables or endpoints,
when it is refreshed, and a typical query.

Until this list exists, use only source names the person gives you and that
`ctzd env --json` accepts. Never guess a source name; if the person does not
know it, they ask the FI champion.

## Conventions

TODO (FI champion): the FI house conventions for:

- prices: per 100 of par, clean or dirty by default;
- yields and spreads: decimal fraction, percent, or basis points in each source;
- dates: which date each source's prices are stamped with (trade, settlement,
  or close of business), and the holiday calendar;
- the preferred identifier (ISIN or CUSIP) and how to join sources on it;
- sector, rating, and currency classifications;
- averages: simple or weighted (by market value or notional).

Until then: ask the person when one of these decides a number, label units on
every column and chart ("Yield (%)", "Price per 100"), and never mix units in
one column.

## Glossary

| Term | Meaning |
|---|---|
| ISIN | 12-character international security identifier: 2-letter country code, 9 alphanumeric characters, 1 check digit. |
| CUSIP | 9-character identifier for North American securities. A US ISIN is `US` + the CUSIP + a check digit. |
| Par (face value) | The amount repaid at maturity; coupons and prices are quoted against it. |
| Price per 100 par | Price as a percentage of par: 98.5 means 98.5% of face value. Market value = price / 100 × par held. |
| Clean price | Quoted price without accrued interest. |
| Accrued interest | Coupon interest earned since the last coupon date and owed to the seller. |
| Dirty price | Clean price plus accrued interest: what the buyer pays. |
| Coupon | Annual interest rate on par, paid at a frequency (often semi-annual); 5% semi-annual pays 2.5 per 100 twice a year. |
| Maturity | The date par is repaid. |
| Yield to maturity | The single rate that discounts all remaining payments to the dirty price. Often stored as a decimal fraction (0.0425 = 4.25%); show it as a percent. |
| Basis point (bp) | 0.01%, or 0.0001 as a decimal fraction. |
| Spread | Yield above a benchmark (a government bond or swap curve), usually in basis points. |
| Duration | Price sensitivity to yield. Modified duration 6 means about a 6% price fall for a 1-percentage-point rise in yield. |
| DV01 | Price change for a 1 bp move in yield, in currency. |
| Trade date | The day a trade is agreed. |
| Settlement date | The day cash and bonds change hands (for example T+1 or T+2 business days); accrued interest runs to this date. |
| Notional | The par amount a position or trade is for. |
