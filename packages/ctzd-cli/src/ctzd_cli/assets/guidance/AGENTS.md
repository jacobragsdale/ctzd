# Instructions for AI agents

This is a ctzd app. The person you are working with is not a programmer: do
every technical step yourself, explain results in plain language, and never
ask them to run a command, open a terminal, use git, or read code.

## Commands

Use the `ctzd` command for everything, always with `--json`:

- `ctzd dev` starts the dashboard locally in the background and returns its `url`; run it again
  after a change, and `ctzd dev --stop` when you are done. `ctzd run JOB --local` runs a job locally.
- `ctzd exec --json -- python -c "..."` runs a command with the app's credentials, such as a quick
  look at what a loader returns from the live source.
- `ctzd env` fetches read-only credentials for the sources in `app.toml`.
- `ctzd check` runs every gate. Run it before you say you are done.
- `ctzd deploy` checks, commits, pushes, and returns the live URL.
- `ctzd status`, `ctzd logs`, `ctzd releases`, `ctzd rollback` for running apps.
- `ctzd add job NAME --schedule "CRON" --timezone ZONE` adds a scheduled job that runs in ZONE
  (such as `Europe/London`), clock changes included.

## Shipping

- When the person says the live app is wrong, run `ctzd rollback --json` first, so they see the
  last good version again; then fix the change and deploy.

## Talking with the person

- Say what changed for them, with the link. Leave out the words they do not use: commit, push,
  branch, build, Jenkins, pipeline, repository, GitHub, lint, tests, coverage, smoke test, cron,
  UTC, port, `.env`, file paths, and command names. Say "the previous version", not "v3".
- Never pass on a raw error or log; say in plain words what went wrong and what happens next.
- Ask only what the person knows: what they want to see, for whom, and when. Never ask where
  the data lives; the app's `sbu-<sbu>` skill and `ctzd sources` tell you.

## Where code goes

- `src/app/data/`: loaders. The only code that reads or writes data. SQL lives in `src/app/data/sql/*.sql`.
- `src/app/logic/`: pure functions, DataFrames in and out. No I/O, no `ctzd`, no Streamlit or Dash.
- `src/app/ui/main.py`: the page. Calls loaders, passes frames to logic, displays results.
- `src/app/jobs/<name>.py`: one scheduled job per module, each with `main() -> int`.
- `tests/`: tests for logic, loaders, the page, and jobs.
- `app.toml`: app settings, sources, and job schedules.

`ctzd check` enforces these layers; do not work around them.

## Rules

- Never open, print, copy, or source `.env`, and never pass it to a program yourself; it holds
  credentials. `ctzd dev`, `ctzd run --local`, and `ctzd exec` load it for you.
- Never edit platform-owned files: this file, `.agents/`, `.claude/`, `Dockerfile`, `Jenkinsfile`,
  `ruff.toml`, `ty.toml`, `.importlinter`, `pytest.toml`, `.pre-commit-config.yaml`,
  `.gitignore`, `.gitattributes`, `.dockerignore`, `.python-version`, `.ctzd/`. Fix the code instead.
- Never add `# noqa`, `# ty: ignore`, or lint exclusions to make a check pass.
- Every number is a float64. Never round one or format it as text outside the page code: no
  `ROUND` or decimal casts in SQL, no `round()`, `.round()`, or `f"{x:.2f}"` in Python. The page
  shows each number to its decimals; `ctzd.display` writes numbers into emails and messages.
  Select ids and account numbers as text.
- Add packages with `uv add NAME`; never edit `uv.lock` by hand.
- Never commit, push, reset, or rewrite history with git yourself: `ctzd deploy` saves and ships
  every change after its checks, and a push of your own would ship code that skipped them.
  Reading history (`git log`, `git show`) is fine.
- Never stop processes with `kill` or `pkill`, and never search this computer for apps, data, or
  credentials; `ctzd` does both for you.
- Dashboard loaders must be fast: cache them with `@ctzd.cached`, and move any query
  slower than about 3 seconds into a job that writes a table with `ctzd.write_table`.

## Skills

Read the skill that matches the task before starting it. They are in `.agents/skills/`:
`ctzd-workflow`, `ctzd-layout`, `ctzd-data`, `ctzd-streamlit`, `ctzd-dash`, `ctzd-jobs`,
`ctzd-testing`, `python-standards`, `ctzd-troubleshoot`, and the skill for this app's
business unit, `sbu-<sbu>`.
