"""Daily positions' rules, as pure functions of frames: each account's net quantity and gross notional in each
security per trade date, and each account's latest activity, from the day's trades. Nothing here reads or writes, so
tests check them on a few rows."""

import pandas as pd

SIDES = frozenset({"BUY", "SELL"})


def problems(trades: pd.DataFrame, accounts: pd.DataFrame) -> list[str]:
    """Reasons not to write the day's tables."""
    found: list[str] = []
    if unknown := int((~trades["account_id"].isin(accounts["account_id"])).sum()):
        found.append(f"{unknown} trades are in accounts the accounts table does not list")
    if sides := set(trades["side"]) - SIDES:
        found.append(f"trades have sides other than BUY and SELL: {sorted(sides)}")
    return found


def daily_positions(trades: pd.DataFrame, accounts: pd.DataFrame, *, include_closed: bool = False) -> pd.DataFrame:
    """Each account's net quantity, gross notional and number of trades in each security, per trade date."""
    kept = trades.merge(accounts[["account_id", "closed"]], on="account_id", how="inner", validate="m:1")
    if not include_closed:
        kept = kept[~kept["closed"].astype(bool)]
    signed = kept["quantity"].where(kept["side"] == "BUY", -kept["quantity"])
    kept = kept.assign(signed=signed, notional=kept["quantity"] * kept["price"])
    grouped = kept.groupby(["trade_date", "account_id", "security_id"], as_index=False).agg(net_quantity=("signed", "sum"), gross_notional=("notional", "sum"), trades=("trade_id", "size"))
    return grouped.assign(trades=grouped["trades"].astype("float64")).sort_values(["trade_date", "account_id", "security_id"], ignore_index=True)


def account_activity(trades: pd.DataFrame) -> pd.DataFrame:
    """Each account's last trade date and when its last trade was booked."""
    found = trades.groupby("account_id", as_index=False).agg(last_trade_date=("trade_date", "max"), last_booked_at=("booked_at", "max"))
    return found.sort_values("account_id", ignore_index=True)
