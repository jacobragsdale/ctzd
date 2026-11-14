# Working on the ctzd platform

This repository is the platform itself, not a citizen app. Read `PLAN.md` before changing behaviour: it records
the agreed decisions, and code that departs from it needs the plan updated in the same change.

## Layout

| Path | What it is |
|---|---|
| `packages/ctzd-cli` | The `ctzd` CLI, and in `src/ctzd_cli/assets/` everything it writes into apps: `templates/dash`, `templates/job`, `locked/`, `guidance/` and `skills/`. `src/ctzd_cli/inapp/` is code the CLI runs inside an app's own environment (the page driving, checks and `render`). |
| `packages/ctzd-kit` | `ctzd-kit`, which every kit builds on and only kits import: the loader seam, the fakes, the store, the drivers, the source explorer |
| `kits/common` | `ctzd-common`: email, Slack, display rounding, and the dashboard's pins as its `dash` extra |
| `kits/fi`, `kits/mas` | Two made-up departments' kits, the worked examples for maintainers |
| `.agents/skills/ctzd-kits` | The skill for a kit maintainer's agent |
| `deploy/helm/ctzd-app`, `airflow/`, `images/base` | The chart, the factory and janitor DAGs (their own uv project), the base image |
| `install/`, `bootstrap/skills/ctzd` | The Windows installer and the one bootstrap skill |
| `validation/` | The validation harness |
| `scripts/e2e.sh` | One app of each template on each kit, made offline and checked |

## Rules

- Public repository: hosts, organisations and people are `contoso` placeholders, and the kits here are made-up
  departments. Never add a real company, team, person, host, source or table.
- Python 3.14 and pandas 3. Python follows the house standard in `pyproject.toml` (ruff with every rule, ty with every
  rule as an error). Fix findings; do not add ignores.
- `packages/ctzd-cli/src/ctzd_cli/assets/` ships inside the CLI. `locked/` is committed in apps and drift-checked
  against the CLI version in an app's `.ctzd/version`; `guidance/` and `skills/` are written into apps and gitignored;
  `templates/` is written once by `ctzd new`. Apps get a change there only through a new CLI version: raise `version`
  in `packages/ctzd-cli/pyproject.toml`. Template Python must already be in ruff-format output; `scripts/e2e.sh`
  proves it.
- An app reads data only through its department's kit. Kits build on `ctzd-kit`; apps never import it.
- Gateways to outside systems (the drivers in `ctzd-kit`, GitHub and Key Vault in the CLI) stay thin; logic lives in
  code that tests drive with fakes (`ctzd_kit.testing`, the CLI's test fakes).

## Checks

```sh
uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest
uv run ctzd kit check kits/fi && uv run ctzd kit check kits/mas
scripts/e2e.sh
(cd airflow && uv run pytest && uv run --with ty==0.0.95 ty check dags tests)
```
