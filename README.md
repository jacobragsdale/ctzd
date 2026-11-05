# ctzd

ctzd is a platform for citizen developers: business users with no coding experience who ask an AI
coding agent to build Python dashboards (Streamlit or Dash) and scheduled jobs, and get back a link to
the running app. The agent does every technical step through the `ctzd` command line. Apps run on
Kubernetes; Jenkins builds them; Airflow schedules their jobs.

The design, every decision behind it, and the rollout plan are in [docs/plan.md](docs/plan.md).
All hostnames and organisation names in this repository are `contoso` placeholders.

## What is in this repository

| Path | What it is |
|---|---|
| `packages/ctzd-cli` | The `ctzd` CLI (distribution `ctzd`, module `ctzd_cli`), including the app template and agent skills it copies into every app (`src/ctzd_cli/assets`). |
| `packages/ctzd-core` | The runtime library apps import as `ctzd`: sources, caching, the table store, notifications, test fakes. |
| `packages/ctzd-api` | The platform API the CLI calls: repo creation, build status, rollback, job runs, logs, credentials. |
| `kits/fi` | `ctzd-fi`, the Fixed Income business unit's library. One such library per business unit. |
| `bootstrap/skills/ctzd` | The one skill the company's skills app distributes; it sets ctzd up and starts apps. |
| `install/install.ps1` | The per-user Windows installer the agent runs. |
| `images/base` | The base image every app image builds on. |
| `airflow/` | The factory DAG that turns job specs into DAGs, with tests against Airflow 3 (its own uv project). |
| `deploy/helm/ctzd-app` | Helm chart for one dashboard (proposal for the infrastructure owner). |
| `jenkins/` | Shared library and rollback/delete jobs (proposals for the infrastructure owner). |
| `scripts/e2e.sh` | Generates one app of each kind and runs `ctzd check` in it. |

## Status

Version 0.1.0, built 2026-11-04. Verified here: the CLI, runtime library, and API test suites; apps of
each kind generated and passing every gate; app images built on the base image, run, and checked with
the browser smoke test; the Helm chart linted and rendered; the factory DAG loaded by Airflow 3.3.
Not yet run against the real services: GitHub Enterprise, Jenkins, AKS, Key Vault, Airflow's REST API,
Oracle, SQL Server, and Windows laptops. Section 13 of the plan lists the Phase 0 checks for each.

## Developing

```sh
uv sync
uv run ruff format . && uv run ruff check .
uv run ty check
uv run pytest
scripts/e2e.sh                        # app template, end to end (needs pypi.org)
(cd airflow && uv sync && uv run pytest)  # factory DAG against a real Airflow
```
