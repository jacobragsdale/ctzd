"""Loaders: the only code in this app that reads data.

Replace `load_demo` with real loaders. A database loader looks like this:

    @ctzd.cached(ttl="15m")
    def load_prices(as_of: dt.date) -> pd.DataFrame:
        frame = ctzd.query("fi_pricing", "sql/prices.sql", {"as_of": as_of})
        return ctzd.require_columns(frame, {"isin": "str", "price": "float"})

and reads `src/app/data/sql/prices.sql`. List each source in `[sources].use`
in app.toml so `ctzd env` can fetch its credentials.
"""

import ctzd
import pandas as pd


@ctzd.cached(ttl="15m")
def load_demo() -> pd.DataFrame:
    return pd.DataFrame({"category": ["North", "South", "East", "West"], "value": [42.0, 17.0, 23.0, 8.0]})
