FI's conventions, which every FI app follows:

- A bond is named by its ISIN, always as text. A book is one of Core Rates, Credit, Liquidity and Municipals.
- Yields, spreads and coupons are fractions: 0.0421 is 4.21%. Prices are per 100 of par. DV01 is US dollars per basis
  point.
- Prices are published once a day, for the close; ask for a business day's prices, never today's before 07:00 London.
- For a year fraction, use `ctzd_fi.helpers.daycount.year_fraction`, which `logic/` may import.
