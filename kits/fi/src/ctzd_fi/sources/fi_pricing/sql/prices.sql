-- Every bond's published price on a day. Ids are text; every number is as the pricing team stores it.
select isin, issuer, sector, clean_price, ytw, oas, as_of
from fi_prices.bond_prices
where as_of = :as_of
