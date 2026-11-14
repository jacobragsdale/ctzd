# ctzd

ctzd is a platform for citizen developers: business users with no coding experience who ask an AI coding agent to
build dashboards and scheduled jobs, and get back a link to the running app. A dashboard is a Dash app in Python; a
job is a Python module that Airflow runs. The agent does every technical step through the `ctzd` command line, and
reads data only through its department's kit.

The design, every decision behind it, the order of work and the rollout are in [PLAN.md](PLAN.md). All hostnames,
organisations and people in this repository are `contoso` placeholders, and its kits are made-up departments.

## What is in this repository

| Path | What it is |
|---|---|
| `packages/ctzd-cli` | The `ctzd` CLI (distribution `ctzd`, module `ctzd_cli`), with the templates, locked build files and skills it writes into apps |
| `packages/ctzd-kit` | `ctzd-kit`, what every department's kit builds on: the loader seam, fakes, the store, drivers, the source explorer |
| `kits/common` | `ctzd-common`: email, Slack and display rounding for apps, and the dashboard's pinned stack |
| `kits/fi`, `kits/mas` | Two made-up departments' kits, the worked examples for kit maintainers |
| `bootstrap/skills/ctzd` | The one skill the firm's skills platform distributes; it sets ctzd up |
| `install/install.ps1` | The per-user Windows installer the agent runs |
| `images/base` | The base image every app image builds on |
| `airflow/` | The factory DAG that turns job specs into DAGs, and the janitor (its own uv project) |
| `deploy/helm/ctzd-app` | The Helm chart for one app |
| `scripts/e2e.sh` | Makes one app of each template on each kit, offline, and runs `ctzd check` in each |
| `validation/` | The validation harness ([docs/validation-plan.md](docs/validation-plan.md)) |

## Status

The rebuild in [PLAN.md](PLAN.md) is under way; section 15 lists its steps. What runs today is recorded in the
commit history: each step's commit says what it verified and what it did not. Nothing has run against GitHub, Azure,
a Windows desktop or Cursor.

## Developing

```sh
uv sync
uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest
uv run ctzd kit check kits/fi && uv run ctzd kit check kits/mas
scripts/e2e.sh                            # both templates on both kits, end to end (needs pypi.org)
(cd airflow && uv sync && uv run pytest)  # factory and janitor DAGs against a real Airflow
```
