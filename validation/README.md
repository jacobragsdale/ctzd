# Validation

How to run the validation stages of [docs/validation-plan.md](../docs/validation-plan.md).
Results of the last run are in that page's section 7. Everything here runs on one Linux
machine with Docker; nothing reaches a real company system.

Put work folders on the same disk as uv's cache (`~/.cache/uv`), not on a `tmpfs` `/tmp`:
every build makes a virtual environment, and uv only hardlinks within one filesystem.

## Stage 0: the existing checks

```sh
uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest
scripts/e2e.sh
scripts/driver-spike.sh
(cd airflow && uv run pytest && uv run --with ty==0.0.95 ty check dags tests)
```

## Stage 1: the CLI against the API over HTTP

```sh
uv run python -m validation.stage1_http ~/.cache/ctzd-validation/stage1
```

## Stage 2: Jenkins and the Helm chart

```sh
docker build -t ctzd-validation-jenkins validation/jenkins
uv run python -m validation.stage2_jenkins ~/.cache/ctzd-validation/stage2
docker rm -f ctzd-validation-jenkins
uv run python -m validation.stage2_helm PATH/TO/helm ~/.cache/ctzd-validation/helm
```

## Stage 3: agent trials

1. Start and seed the shared data containers (Oracle, SQL Server, Mailpit) once:

   ```sh
   uv run python -m validation.platform.serve data ~/.cache/ctzd-validation/trials
   ```

   Or, on a host short of memory, run on fake data: no containers, and every database source
   answers from each app's own `fixtures/` folder, on the laptop and live. The FI skill tells
   the agent so, and `score` has no right answers to compare with.

   ```sh
   uv run python -m validation.platform.serve fake-data ~/.cache/ctzd-validation/trials-fake
   ```

2. Install the CLI the agents use, and pnpm, on their own, so their `PATH` holds only these:

   ```sh
   V=~/.cache/ctzd-validation/trials
   UV_TOOL_DIR=$V/tool UV_TOOL_BIN_DIR=$V/tool/bin uv tool install --force packages/ctzd-cli
   npm install --global --prefix $V/tool pnpm@12.18.0
   ```

   Reinstall the CLI after every change to it. The React pages install basis-ui's packages
   from the `file:` tarballs the bundled `platform.toml` names: each platform copies them into
   `$V/npm` and serves them at `/npm/`, as the internal registry will.

3. Check the local platform with the scripted flow, then run the canaries:

   ```sh
   .venv/bin/python -m validation.trials probe $V
   .venv/bin/python -m validation.trials run $V s00-canary s00-canary-in-app
   ```

4. Run trials. `SCENARIO[:PERSONA]` picks one (see `validation/scenarios.py`); without a
   persona they rotate. `--model` sets the agent, `--person-model` the simulated person.

   ```sh
   .venv/bin/python -m validation.trials run $V s01 s02 s03 --reps 3 --parallel 3
   .venv/bin/python -m validation.trials run $V s01 s05 --model claude-sonnet-5-5
   ```

5. Score them. This writes `scores.md`, `scores.json`, `expected.json` (the right answers,
   from the seeded tables), and one digest per trial under `digests/` for graders. Each
   trial's row gives the minutes until the first link reached the person (and the first live
   link), the agent's tokens, and the technical words in what the person read:

   ```sh
   .venv/bin/python -m validation.score $V
   ```

Trials run headless Claude Code with the operator's login (found through
`CLAUDE_CONFIG_DIR`) but a fresh `HOME`, no user settings, skills, or MCP servers, and deny
rules for `.env` and the operator's own folders, inside a bubblewrap (`bwrap`) sandbox that
shows each agent only its own trial. A Haiku trial costs a few cents to about $0.80. Haiku
plays the person by default and sometimes stalls or invents facts; `--person-model
claude-sonnet-5-5` gives steadier conversations.

The data containers keep running until you remove them:
`docker rm -f ctzd-trial-oracle ctzd-trial-mssql ctzd-trial-mailpit`.
