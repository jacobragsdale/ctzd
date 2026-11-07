"""The trial data sources: fixed-income tables in Oracle and SQL Server, and a REST API of new issues.

Every value comes from a seeded random generator, so the scorer can compute the
right answer to each scenario from the same tables the agent queried.
"""

import datetime as dt
import random
import secrets
import subprocess
import time
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import parse_qs

import mssql_python
import oracledb
from fastapi import FastAPI, Header, HTTPException, Request

from validation.harness import tool
from validation.platform.cluster import LocalCluster

SEED = 20261106
TODAY = dt.date(2026, 11, 6)
SECTORS = ("Financials", "Utilities", "Industrials", "Technology", "Energy", "Government", "Healthcare", "Telecom")
RATINGS = ("AAA", "AA", "A", "BBB", "BB", "B")
ISSUERS = ("Contoso Bank", "Fabrikam Power", "Northwind Steel", "Tailspin Air", "Litware Energy", "Republic of Contoso", "Adatum Health", "Proseware Telecom", "Woodgrove Finance", "Alpine Grid")
ORACLE_PORT = 15211
MSSQL_PORT = 15434
SMTP_PORT = 11025
MAILPIT_PORT = 18025
ORACLE = "ctzd-trial-oracle"
MSSQL = "ctzd-trial-mssql"
MAILPIT = "ctzd-trial-mailpit"
OAUTH_CLIENT = "ctzd-fi"
FRIDAY = 4


@dataclass(frozen=True)
class Bond:
    isin: str
    issuer: str
    sector: str
    rating: str
    coupon_pct: float
    maturity: dt.date


def business_days(end: dt.date, count: int) -> list[dt.date]:
    days: list[dt.date] = []
    day = end
    while len(days) < count:
        if day.weekday() <= FRIDAY:
            days.append(day)
        day -= dt.timedelta(days=1)
    return sorted(days)


def bonds() -> list[Bond]:
    rng = random.Random(SEED)  # noqa: S311 - test data, not security
    found: list[Bond] = []
    for index in range(120):
        sector = SECTORS[index % len(SECTORS)]
        issuer = "Republic of Contoso" if sector == "Government" else rng.choice(ISSUERS[:5] + ISSUERS[6:])
        maturity = dt.date(rng.randint(2027, 2045), rng.randint(1, 12), 15)
        found.append(Bond(f"XS{2600000000 + index * 7919:010d}", issuer, sector, rng.choice(RATINGS), round(rng.uniform(1.0, 7.0), 3), maturity))
    return found


def price_rows() -> list[tuple[object, ...]]:
    """(isin, issuer, sector, rating, coupon_pct, maturity_date, clean_price, yield_dec, as_of_date)."""
    rng = random.Random(SEED + 1)  # noqa: S311 - test data, not security
    rows: list[tuple[object, ...]] = []
    for bond in bonds():
        price = rng.uniform(88, 108)
        for day in business_days(TODAY, 10):
            price = max(70.0, price + rng.gauss(0, 0.35))
            years = max(0.5, (bond.maturity - day).days / 365.25)
            yield_dec = round(max(0.001, bond.coupon_pct / 100 + (100 - price) / 100 / years), 6)
            rows.append((bond.isin, bond.issuer, bond.sector, bond.rating, bond.coupon_pct, bond.maturity, round(price, 4), yield_dec, day))
    return rows


def trade_rows() -> list[tuple[object, ...]]:
    """(trade_id, trade_date, isin, side, notional, price, desk, trader)."""
    rng = random.Random(SEED + 2)  # noqa: S311 - test data, not security
    held = bonds()
    desks = ("Credit", "Rates", "Emerging Markets")
    traders = ("a.okafor", "b.ruiz", "c.nakamura", "d.weber", "e.silva")
    return [
        (
            number,
            rng.choice(business_days(TODAY, 30)),
            rng.choice(held).isin,
            rng.choice(("BUY", "SELL")),
            rng.choice((1, 2, 5, 10, 25)) * 1_000_000,
            round(rng.uniform(88, 108), 4),
            rng.choice(desks),
            rng.choice(traders),
        )
        for number in range(1, 2001)
    ]


def new_issues() -> list[dict[str, object]]:
    rng = random.Random(SEED + 3)  # noqa: S311 - test data, not security
    issues: list[dict[str, object]] = []
    for index in range(87):
        priced = TODAY - dt.timedelta(days=rng.randint(0, 60))
        issues.append(
            {
                "isin": f"XS{2700000000 + index * 104729:010d}",
                "issuer": rng.choice(ISSUERS),
                "rating": rng.choice(RATINGS),
                "coupon_pct": round(rng.uniform(2.0, 8.0), 3),
                "maturity_date": dt.date(rng.randint(2028, 2046), rng.randint(1, 12), 1).isoformat(),
                "size_mm": rng.choice((250, 300, 500, 750, 1000, 1500)),
                "currency": rng.choice(("EUR", "USD", "GBP")),
                "priced_on": priced.isoformat(),
            }
        )
    return sorted(issues, key=lambda issue: str(issue["priced_on"]), reverse=True)


# ---------------------------------------------------------------- containers


def start_containers(password: str) -> None:
    docker = tool("docker")
    for name in (ORACLE, MSSQL, MAILPIT):
        subprocess.run([docker, "rm", "-f", name], capture_output=True, check=False)
    oracle_env = ["-e", f"ORACLE_PASSWORD={password}", "-e", "APP_USER=fi_ro", "-e", f"APP_USER_PASSWORD={password}"]
    subprocess.run([docker, "run", "-d", "--name", ORACLE, "-p", f"127.0.0.1:{ORACLE_PORT}:1521", *oracle_env, "gvenzl/oracle-free:23-slim-faststart"], check=True, capture_output=True)
    mssql_env = ["-e", "ACCEPT_EULA=Y", "-e", f"MSSQL_SA_PASSWORD={password}"]
    subprocess.run([docker, "run", "-d", "--name", MSSQL, "-p", f"127.0.0.1:{MSSQL_PORT}:1433", *mssql_env, "mcr.microsoft.com/mssql/server:2022-latest"], check=True, capture_output=True)
    mail_ports = ["-p", f"127.0.0.1:{SMTP_PORT}:1025", "-p", f"127.0.0.1:{MAILPIT_PORT}:8025"]
    subprocess.run([docker, "run", "-d", "--name", MAILPIT, *mail_ports, "axllent/mailpit:v1.27"], check=True, capture_output=True)
    for name, ready in ((ORACLE, "DATABASE IS READY TO USE"), (MSSQL, "SQL Server is now ready for client connections")):
        deadline = time.monotonic() + 600
        while (
            ready
            not in subprocess.run([docker, "logs", name], capture_output=True, text=True, check=False).stdout
            + subprocess.run([docker, "logs", name], capture_output=True, text=True, check=False).stderr
        ):
            if time.monotonic() > deadline:
                msg = f"{name} never became ready"
                raise RuntimeError(msg)
            time.sleep(3)
    time.sleep(5)


def seed_oracle(password: str) -> None:
    with oracledb.connect(user="fi_ro", password=password, dsn=f"127.0.0.1:{ORACLE_PORT}/FREEPDB1") as connection, connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE fi_bond_prices (isin VARCHAR2(12), issuer VARCHAR2(60), sector VARCHAR2(30), rating VARCHAR2(4), coupon_pct NUMBER(6,3),"
            " maturity_date DATE, clean_price NUMBER(10,4), yield_dec NUMBER(10,6), as_of_date DATE)"
        )
        cursor.executemany("INSERT INTO fi_bond_prices VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9)", price_rows())
        # A view that is slow on purpose: dashboards should not query it directly (scenario 4).
        cursor.execute("CREATE OR REPLACE FUNCTION fi_slow_gate RETURN NUMBER IS BEGIN DBMS_SESSION.SLEEP(6); RETURN 1; END;")
        cursor.execute(
            "CREATE OR REPLACE VIEW fi_bond_risk AS SELECT isin, as_of_date, ROUND((maturity_date - as_of_date) / 365.25 / (1 + yield_dec), 4) AS modified_duration,"
            " ROUND(clean_price * ((maturity_date - as_of_date) / 365.25 / (1 + yield_dec)) * 100, 2) AS dv01_per_mm"
            " FROM fi_bond_prices WHERE (SELECT fi_slow_gate() FROM dual) = 1"
        )
        connection.commit()


def seed_mssql(password: str) -> None:
    with mssql_python.connect(f"Server=127.0.0.1,{MSSQL_PORT};Database=master;UID=sa;PWD={password};Encrypt=yes;TrustServerCertificate=yes") as connection:
        cursor = connection.cursor()
        connection.autocommit = True
        cursor.execute("IF DB_ID('fi_trading') IS NULL CREATE DATABASE fi_trading")
        cursor.execute("USE fi_trading")
        cursor.execute(
            "CREATE TABLE dbo.trades (trade_id INT PRIMARY KEY, trade_date DATE, isin VARCHAR(12), side VARCHAR(4), notional DECIMAL(18,2), price DECIMAL(10,4), desk VARCHAR(30), trader VARCHAR(30))"
        )
        rows = trade_rows()
        for start in range(0, len(rows), 500):
            cursor.executemany("INSERT INTO dbo.trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows[start : start + 500])


# ---------------------------------------------------------------- the REST source and GitHub's /user


def rest_app(client_secret: str, logins: dict[str, tuple[str, str, str]], cluster: LocalCluster) -> FastAPI:
    """The `fi_issuance` REST source (OAuth client credentials, paged), a Teams webhook, GitHub's `/user`, and what the scorer reads."""
    api = FastAPI()
    tokens: set[str] = set()
    issues = new_issues()
    teams: list[object] = []

    @api.post("/teams/webhook")
    async def teams_webhook(request: Request) -> dict[str, str]:
        teams.append(await request.json())
        return {"status": "accepted"}

    @api.get("/local/teams")
    def teams_messages() -> list[object]:
        return teams

    @api.get("/local/dashboards")
    def dashboards() -> dict[str, int]:
        return {repo: record.dashboard.port for repo, record in cluster.apps.items() if record.dashboard is not None}

    @api.get("/local/runs")
    def runs() -> dict[str, dict[str, str]]:
        return {run_id: {"dag_id": run.dag_id, "state": run.state} for run_id, run in cluster.runs.items()}

    @api.post("/issuance/oauth/token")
    async def token(request: Request) -> dict[str, object]:
        form = {key: values[0] for key, values in parse_qs((await request.body()).decode()).items()}
        if form.get("grant_type") != "client_credentials" or form.get("client_id") != OAUTH_CLIENT or form.get("client_secret") != client_secret:
            raise HTTPException(status_code=401, detail="invalid client")
        issued = secrets.token_hex(16)
        tokens.add(issued)
        return {"access_token": issued, "token_type": "Bearer", "expires_in": 3600}

    @api.get("/issuance/v1/new-issues")
    def new_issue_page(request: Request, authorization: Annotated[str | None, Header()] = None, since: str | None = None, cursor: int = 0, page_size: int = 25) -> dict[str, object]:
        if authorization is None or authorization.removeprefix("Bearer ") not in tokens:
            raise HTTPException(status_code=401, detail="missing or expired token")
        chosen = [issue for issue in issues if since is None or str(issue["priced_on"]) >= since]
        size = max(1, min(page_size, 50))
        page = chosen[cursor : cursor + size]
        following = cursor + size
        query = f"cursor={following}&page_size={size}" + ("" if since is None else f"&since={since}")
        return {"items": page, "next": f"{str(request.base_url).rstrip('/')}/issuance/v1/new-issues?{query}" if following < len(chosen) else None}

    @api.get("/github/api/v3/user")
    def user(authorization: Annotated[str | None, Header()] = None) -> dict[str, object]:
        found = logins.get((authorization or "").removeprefix("Bearer "))
        if found is None:
            raise HTTPException(status_code=401, detail="Bad credentials")
        login, name, email = found
        return {"login": login, "name": name, "email": email}

    return api
