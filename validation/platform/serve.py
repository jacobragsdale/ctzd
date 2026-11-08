"""Run the local platform.

    uv run python -m validation.platform.serve data ROOT        # start and seed the shared data containers once
    uv run python -m validation.platform.serve fake-data ROOT   # or: no containers, every source answers from fixtures/
    uv run python -m validation.platform.serve platform ROOT    # one isolated platform (API, repos, builds) per trial

`platform` writes ROOT/platform.json with the port and the environment a
trial's agent and person need, then serves until it is stopped.

On fake data, the databases are not started: each live app serves the rows in
its own `fixtures/` folder, as `ctzd dev --fake` does on the laptop, and the FI
skill tells the agent so. The REST source, Teams, and email still answer.
"""

import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from starlette.staticfiles import StaticFiles

from ctzd.api import FAKE_FIXTURES
from ctzd_api import fakes
from ctzd_api.app import create_app
from ctzd_api.releases import ReleaseStore
from ctzd_api.service import Services
from ctzd_cli import config, releases
from validation.harness import build_wheels, npm_packages, platform_config
from validation.platform import data
from validation.platform.cluster import LocalAirflow, LocalCluster, LocalGitHub, LocalJenkins, LocalKube, free_port

GITHUB_HOST = "github.ctzd.test"
BOT_TOKEN = "platform-bot-token"  # noqa: S105 - a login of the local GitHub stand-in, not a credential
PERSON_TOKEN = "ann-token"  # noqa: S105 - a login of the local GitHub stand-in, not a credential
PERSON = ("ann.lee", "Ann Lee", "ann.lee@contoso.com")
FI_SOURCES = """## Sources

| Name in `[sources].use` | Kind | What it holds |
|---|---|---|
| `fi_pricing` | `oracle` | End-of-day bond prices, one row per bond per business day, last 10 business days. |
| `fi_trading` | `mssql` | The FI desks' trades for the last 30 business days. |
| `fi_issuance` | `rest` | New bond issues priced in the last 60 days. |
| `fi_alerts` | `teams` | The FI desk's Teams channel, for notifications. |

**`fi_pricing`** has one table, `fi_bond_prices`: `isin`, `issuer`, `sector`,
`rating`, `coupon_pct` (percent), `maturity_date`, `clean_price` (per 100 par),
`yield_dec` (decimal fraction: 0.0425 is 4.25%), `as_of_date` (close of
business). The view `fi_bond_risk` (`isin`, `as_of_date`, `modified_duration`,
`dv01_per_mm`) takes several seconds to answer: query it from a job, never from
a dashboard.

**`fi_trading`** has `dbo.trades`: `trade_id`, `trade_date`, `isin`, `side`
(`BUY` or `SELL`), `notional` (currency), `price` (per 100 par), `desk`,
`trader`. Bind parameters are `%(name)s`.

**`fi_issuance`**: `GET /v1/new-issues?since=YYYY-MM-DD&page_size=25` returns
`{"items": [...], "next": URL or null}`; use `get_all_pages(..., records="items")`.
`since` is optional: without it the feed returns every issue it holds, the last
60 days.
Each item has `isin`, `issuer`, `rating`, `coupon_pct`, `maturity_date`,
`size_mm` (millions), `currency`, `priced_on`.

"""

FAKE_DATA_NOTE = """**The FI databases are being moved this week: `fi_pricing` and `fi_trading`
cannot be reached from any laptop or app, and every connection to them is
refused.** Do not try them, wait for them, or retry them. Until the move is
done, both sources answer from each app's own `fixtures/` folder, on this
laptop (`ctzd dev --fake`) and in the live app alike: write made-up rows with
the columns below in `fixtures/SOURCE/NAME.csv`, build on those, and deploy on
them when the person agrees. The person knows about the move and expects
made-up rows for now; the live app switches to real data by itself once the
move is done, with no change to the app.

"""


SEED_RELEASE = releases.seed().release
TRIAL_RELEASE = SEED_RELEASE + 1
"""The trials' platform serves the seed and, as both channels' current release, the seed with FI's sources filled in."""


def trial_release(*, fake_data: bool = False) -> dict[str, str]:
    """The release after the seed: the seed with the FI champion's sources filled in, as before the pilot."""
    files = releases.seed_files()
    skill_path = "skills/sbu/fi/sbu-fi/SKILL.md"
    skill = files[skill_path]
    start = skill.index("## Sources")
    end = skill.index("## Conventions")
    manifest = re.sub(r"^release = \d+$", f"release = {TRIAL_RELEASE}", files["release.toml"], count=1, flags=re.MULTILINE)
    sources = FI_SOURCES.replace("\n\n**`fi_pricing`**", "\n\n" + FAKE_DATA_NOTE + "**`fi_pricing`**", 1) if fake_data else FI_SOURCES
    return files | {"release.toml": manifest, skill_path: skill[:start] + sources + skill[end:]}


@dataclass(frozen=True)
class DataSources:
    password: str
    oauth_secret: str
    fake: bool = False


def start_data(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    found = DataSources(password=f"Ctzd-trial-{secrets.token_hex(6)}", oauth_secret=secrets.token_hex(12))
    data.start_containers(found.password)
    data.seed_oracle(found.password)
    data.seed_mssql(found.password)
    (root / "data.json").write_text(json.dumps({"password": found.password, "oauth_secret": found.oauth_secret}))


def fake_data(root: Path) -> None:
    """No databases: every source answers from the app's own fixtures, on the laptop and live."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "data.json").write_text(json.dumps({"password": "", "oauth_secret": secrets.token_hex(12), "fake": True}))


def source_values(sources: DataSources, port: int) -> dict[str, str]:
    base = f"http://127.0.0.1:{port}"
    values: dict[str, object] = {
        "fi_pricing": {"kind": "oracle", "dsn": f"127.0.0.1:{data.ORACLE_PORT}/FREEPDB1", "user": "fi_ro", "password": sources.password},
        "fi_trading": {"kind": "mssql", "server": f"127.0.0.1,{data.MSSQL_PORT}", "database": "fi_trading", "user": "sa", "password": sources.password, "trust_server_certificate": True},
        "fi_issuance": {
            "kind": "rest",
            "base_url": f"{base}/issuance",
            "auth": {"type": "client_credentials", "token_url": f"{base}/issuance/oauth/token", "client_id": data.OAUTH_CLIENT, "client_secret": sources.oauth_secret},
        },
        "fi_alerts": {"kind": "teams", "webhook_url": f"{base}/teams/webhook"},
    }
    return {f"ctzd-source-{name.replace('_', '-')}": json.dumps(value) for name, value in values.items()}


def run_platform(root: Path, base: Path) -> None:
    saved = json.loads((base / "data.json").read_text())
    sources = DataSources(password=saved["password"], oauth_secret=saved["oauth_secret"], fake=saved.get("fake") is True)
    port = free_port()
    store = root / "store"
    store.mkdir(parents=True, exist_ok=True)
    extra = {
        'host = "github.contoso.com"': f'host = "{GITHUB_HOST}"',
        'api_url = "https://github.contoso.com/api/v3"': f'api_url = "http://127.0.0.1:{port}/github/api/v3"',
        'apps_domain = "ctzd.contoso.com"': 'apps_domain = "ctzd.test"',
        'airflow_url = "https://airflow.ctzd.contoso.com"': 'airflow_url = "http://airflow.ctzd.test"',
        'store_url = "abfs://ctzd@contosoctzdstore.dfs.core.windows.net"': f'store_url = "{store}"',
    } | npm_packages(base, f"http://127.0.0.1:{port}/npm")
    wheels = base / "wheels"
    if not wheels.is_dir():
        build_wheels(wheels)
    cfg = platform_config(root, wheels, api_url=f"http://127.0.0.1:{port}", extra=extra)
    gitconfig = root / "gitconfig"
    gitconfig.write_text(f'[user]\n\tname = {PERSON[1]}\n\temail = {PERSON[2]}\n[url "{root / "github"}/"]\n\tinsteadOf = https://{GITHUB_HOST}/ctzd-apps/\n[init]\n\tdefaultBranch = main\n')
    secret_values = source_values(sources, port)
    github = LocalGitHub(root=root / "github", tokens={PERSON_TOKEN: PERSON[0], BOT_TOKEN: "ctzd-bot"})
    github.root.mkdir(parents=True, exist_ok=True)

    def runtime_env(secret_names: list[str]) -> dict[str, str]:
        # One sender per platform, so each trial finds its own email among everyone's in the shared Mailpit.
        env = {"CTZD_SMTP_HOST": "127.0.0.1", "CTZD_SMTP_PORT": str(data.SMTP_PORT), "CTZD_SMTP_SENDER": f"ctzd-{port}@contoso.com"}
        if sources.fake:
            env[FAKE_FIXTURES] = "fixtures"  # the app's own folder: dashboards run in their build's workspace
        for name in secret_names:
            if name in secret_values:
                env["CTZD_SOURCE_" + name.removeprefix("ctzd-source-").replace("-", "_").upper()] = secret_values[name]
        return env

    ci_env = {"CTZD_PLATFORM_CONFIG": str(cfg), "CTZD_GITHUB_TOKEN": BOT_TOKEN, "CTZD_HOME": str(root / "ci-home"), "GIT_CONFIG_GLOBAL": str(gitconfig)}
    # The starter app's build is 1; the person's first deploy is 2.
    fail_build = 2 if os.environ.get("CTZD_TRIAL_FAIL_FIRST_BUILD") == "1" else None
    cluster = LocalCluster(github=github, root=root, ci_env=ci_env, runtime_env=runtime_env, fail_build=fail_build)
    seed = releases.seed_files()
    gateway = fakes.FakeReleases(
        channels=f'cli = "0.3.0"\n[channels]\npilot = {TRIAL_RELEASE}\ngeneral = {TRIAL_RELEASE}\n',
        archives={SEED_RELEASE: fakes.archive(seed), TRIAL_RELEASE: fakes.archive(trial_release(fake_data=sources.fake))},
    )
    services = Services(
        github=github,
        jenkins=LocalJenkins(cluster),
        airflow=LocalAirflow(cluster),
        kube=LocalKube(cluster),
        secrets=fakes.FakeSecrets(values=secret_values),
        releases=ReleaseStore(gateway),
        platform=config.parse(cfg.read_text()),
    )
    api = create_app(services)
    extras = data.rest_app(sources.oauth_secret, {PERSON_TOKEN: PERSON, BOT_TOKEN: ("ctzd-bot", "ctzd", "ctzd@localhost")}, cluster)
    api.include_router(extras.router)
    # basis-ui's packages, as the internal npm registry will serve them.
    api.mount("/npm", StaticFiles(directory=base / "npm"), name="npm")
    (root / "platform.json").write_text(
        json.dumps(
            {
                "port": port,
                "env": {"CTZD_PLATFORM_CONFIG": str(cfg), "GIT_CONFIG_GLOBAL": str(gitconfig), "CTZD_GITHUB_TOKEN": PERSON_TOKEN},
                "profile": {"login": PERSON[0], "name": PERSON[1], "email": PERSON[2]},
                "mailpit": f"http://127.0.0.1:{data.MAILPIT_PORT}",
            }
        )
    )
    cluster.start()
    try:
        uvicorn.run(api, host="127.0.0.1", port=port, log_level="warning")
    finally:
        cluster.stop()


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in {"data", "fake-data", "platform"}:  # noqa: PLR2004 - the command and its folder
        sys.stderr.write(__doc__ or "")
        return 2
    root = Path(sys.argv[2]).resolve()
    if sys.argv[1] == "data":
        start_data(root)
    elif sys.argv[1] == "fake-data":
        fake_data(root)
    else:
        root.mkdir(parents=True, exist_ok=True)
        base = next((folder for folder in root.parents if (folder / "data.json").is_file()), None)
        if base is None:
            sys.stderr.write("No data.json above this folder: run `serve data` or `serve fake-data` in a parent folder first.\n")
            return 1
        run_platform(root, base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
