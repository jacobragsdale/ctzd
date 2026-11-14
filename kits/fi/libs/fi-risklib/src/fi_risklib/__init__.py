"""A made-up stand-in for the desk's risk library: a package that reads its own credentials from the environment
(`FI_RISK_USER`, `FI_RISK_PASSWORD`) and makes its own connection, as a department's real one would. Nothing here is
reachable: the host is a contoso placeholder."""

import os
from datetime import date

import httpx
import pandas as pd

HOST = os.environ.get("FI_RISK_URL", "https://risk.fi.contoso.com")


def measures(as_of: date) -> pd.DataFrame:
    """DV01 and spread duration of every position on `as_of`."""
    auth = httpx.BasicAuth(os.environ["FI_RISK_USER"], os.environ["FI_RISK_PASSWORD"])
    response = httpx.get(f"{HOST}/v2/measures", params={"as_of": as_of.isoformat()}, auth=auth, timeout=60.0)
    response.raise_for_status()
    return pd.DataFrame(response.json()["positions"])
