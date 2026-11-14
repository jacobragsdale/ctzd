# Validation

How to run the validation stages of [docs/validation-plan.md](../docs/validation-plan.md) and PLAN.md section 14.
The stages against GitHub, the local stand-ins for Azure and the agent trials are rebuilt in PLAN.md section 15,
steps 6 to 9; until then this folder holds the shared harness, the trial scenarios and the Helm stage.

Put work folders on the same disk as uv's cache (`~/.cache/uv`), not on a `tmpfs` `/tmp`: every app makes a
virtual environment, and uv only hardlinks within one filesystem.

## Stage 0: the existing checks

```sh
uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest
uv run ctzd kit check kits/fi && uv run ctzd kit check kits/mas
scripts/e2e.sh
(cd airflow && uv run pytest && uv run --with ty==0.0.95 ty check dags tests)
```

## The Helm chart

```sh
uv run python -m validation.stage2_helm PATH/TO/helm ~/.cache/ctzd-validation/helm
```
