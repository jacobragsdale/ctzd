# ctzd: sample job. Replace it with the job the person asked for, then delete this line.
"""Daily positions: each account's positions from the last business day's trades, written to the app's tables
`daily_positions` and `account_activity`, and a summary emailed to the app's owners. Airflow calls `main()` on the
schedule in app.toml."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ctzd_common import display, email

from app.data import accounts, trades, write
from app.logic import positions as rules

DESK = ZoneInfo("Europe/London")
SATURDAY = 5
SUMMARY_TO = ("{{owner}}",)
_LOG = logging.getLogger(__name__)


def main() -> int:
    """Run the job once; 0 when it wrote its tables, 1 when it found a reason not to."""
    day = datetime.now(DESK).date() - timedelta(days=1)
    while day.weekday() >= SATURDAY:
        day -= timedelta(days=1)
    booked, known = trades(day, day + timedelta(days=1)), accounts()
    if found := rules.problems(booked, known):
        _LOG.error("Not writing the daily positions for %s: %s", day.isoformat(), "; ".join(found))
        return 1
    positions = rules.daily_positions(booked, known)
    write(positions, "daily_positions")
    write(rules.account_activity(booked), "account_activity")
    formats = {"net_quantity": display.Format("number", 0), "gross_notional": display.Format("money", 0, currency="USD"), "trades": display.Format("number", 0)}
    summary = email.Table(positions, formats, title=f"Daily positions for {day.isoformat()}")
    email.send(list(SUMMARY_TO), f"Daily positions for {day.isoformat()}", "The day's positions, by account and security.", table=summary, attachments={"daily_positions.xlsx": summary})
    return 0
