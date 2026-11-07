# Instructions for AI agents

This is a ctzd app. The person you are working with is not a programmer: do
every technical step yourself, explain results in plain language, and never
ask them to run a command, open a terminal, use git, or read code.

## Commands

Use the `ctzd` command for everything, always with `--json`:

- `ctzd dev` runs the dashboard locally; `ctzd run JOB --local` runs a job locally.
- `ctzd exec --json -- python -c "..."` runs a command with the app's credentials, such as a quick
  look at what a loader returns from the live source.
- `ctzd env` fetches read-only credentials for the sources in `app.toml`.
- `ctzd check` runs every gate. Run it before you say you are done.
- `ctzd deploy` checks, commits, pushes, and returns the live URL.
- `ctzd status`, `ctzd logs`, `ctzd releases`, `ctzd rollback` for running apps.
- `ctzd add job NAME --schedule "CRON" --timezone ZONE` adds a scheduled job that runs in ZONE
  (such as `Europe/London`), clock changes included.

## Shipping

- After every `ctzd deploy`, look at the screenshot named in its `screenshot` field before you
  tell the person it works.
- When the person says the live app is wrong, run `ctzd rollback --json` first, so they see the
  last good version again; then fix the change and deploy.

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
- Add packages with `uv add NAME`; never edit `uv.lock` by hand.
- Dashboard loaders must be fast: cache them with `@ctzd.cached`, and move any query
  slower than about 3 seconds into a job that writes a table with `ctzd.write_table`.

## Skills

Read the skill that matches the task before starting it. They are in `.agents/skills/`:
`ctzd-workflow`, `ctzd-layout`, `ctzd-data`, `ctzd-streamlit`, `ctzd-dash`, `ctzd-jobs`,
`ctzd-testing`, `python-standards`, `ctzd-troubleshoot`, and the skill for this app's
business unit, `sbu-<sbu>`.
