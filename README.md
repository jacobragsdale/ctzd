# ctzd

ctzd is a platform for citizen developers: business users with no coding experience who ask an AI
coding agent to build dashboards and scheduled jobs, and get back a link to the running app. A
dashboard is a React app on basis-ui with a basis-api backend, in one repo and one container.
The agent does every technical step through the `ctzd` command line. Apps run on Kubernetes; Jenkins
builds them; Airflow schedules their jobs.

The design, every decision behind it, and the rollout plan are in [docs/plan.md](docs/plan.md).
All hostnames and organisation names in this repository are `contoso` placeholders.

## What is in this repository

| Path | What it is |
|---|---|
| `packages/ctzd-cli` | The `ctzd` CLI (distribution `ctzd`, module `ctzd_cli`), and the platform release folder (`src/ctzd_cli/assets`): the build files, agent guidance, and starter code the platform API serves from `platform-release/<n>` tags. |
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
| `validation/` | The validation harness: the CLI against the API over HTTP, the Jenkins dry run, a local copy of the platform, and agent trials ([docs/validation-plan.md](docs/validation-plan.md)). |

## Status

CLI 0.3.0, 2026-11-07. Templates and skills ship as platform releases the API serves, separate from the
CLI. Every number is a float64 that only the display rounds, and `ctzd check` refuses rounding in an
app's Python and SQL; agents take no screenshots; dashboards are reached through Gateway API. Verified
here: the CLI, runtime library, and API test suites; apps of each kind generated and passing every
gate; the CLI against the API over HTTP; the Jenkins library, rollback job, and delete job on a real
Jenkins with the deploy tools stubbed; the Helm chart linted and rendered; the factory and janitor DAGs
loaded by Airflow 3.3; and agent trials against a local copy of the platform. Results are in
[docs/validation-plan.md](docs/validation-plan.md). Not yet run against the real services: GitHub
Enterprise, Jenkins with a real cluster, AKS, Key Vault, Airflow's REST API, the company's Oracle and
SQL Server, and Windows laptops. Section 13 of the plan lists the Phase 0 checks for each.

## Developing

```sh
uv sync
uv run ruff format . && uv run ruff check .
uv run ty check
uv run pytest
scripts/e2e.sh                        # app template, end to end (needs pypi.org)
(cd airflow && uv sync && uv run pytest)  # factory and janitor DAGs against a real Airflow
```

The validation stages, slower and needing Docker, are in [validation/README.md](validation/README.md).
