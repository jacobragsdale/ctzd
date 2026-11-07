# Working on the ctzd platform

This repository is the platform itself, not a citizen app. Read `docs/plan.md` before changing
behavior: it records the agreed decisions, and code that departs from it needs the plan updated
in the same change.

## Rules

- Public repository: hosts, organisations, and people are `contoso` placeholders. Never add real
  company, team, or personal names.
- Python follows the house standard as configured in `pyproject.toml` (ruff with every rule, ty with
  every rule as an error). Fix findings; do not add ignores.
- `packages/ctzd-cli/src/ctzd_cli/assets/` is the platform release folder: `locked/` (build files,
  committed in apps and drift-checked), `guidance/` and `skills/` (written into apps, gitignored),
  `starter/`, and `start/`. Apps get a change there only through a new platform release: raise
  `release` in `assets/release.toml`, commit, and tag `platform-release/<n>`. Template Python must
  already be in ruff-format output; `scripts/e2e.sh` proves it.
- The CLI and API share request and response models in `ctzd_cli/api_models.py`.
- Gateways to outside systems (`ctzd_api/clients.py`, drivers in `ctzd-core`) stay thin; logic lives
  in code that tests can drive with the fakes in `ctzd_api/fakes.py` and `ctzd.testing`.

## Checks

```sh
uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest
scripts/e2e.sh
(cd airflow && uv run pytest && uv run --with ty==0.0.95 ty check dags tests)
```
