SELECT isin, sector, clean_price, as_of_date
FROM fi_bond_prices
WHERE as_of_date = (SELECT MAX(as_of_date) FROM fi_bond_prices)
