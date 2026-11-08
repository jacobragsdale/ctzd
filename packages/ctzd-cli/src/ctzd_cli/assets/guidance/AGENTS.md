# Instructions for AI agents

This is a ctzd app. The person you are working with is not a programmer: do
every technical step yourself, explain results in plain language, and never
ask them to run a command, open a terminal, use git, or read code.

## The order of work

1. Ask at most three things, all about the business: what question the page
   answers, who looks at it and how often, and which numbers matter. Never ask
   where the data lives; the app's `sbu-<sbu>` skill and `ctzd sources` tell you.
2. Look at the real columns of the source (`ctzd exec`, see `ctzd-data`), and
   write a few made-up rows with those columns in `fixtures/SOURCE/NAME.csv`.
3. Build the page on those rows: run `ctzd dev --fake`, then `ctzd shot` and look
   at the picture it writes; fix every problem it lists. Then give the person the
   link `ctzd dev` returned, to open on their own machine. Change the page until
   they say it is right, then run `ctzd dev` without `--fake` for the real data.
4. Check a number or two with them against what they see elsewhere.
5. Deploy when they say so (`ctzd deploy`), and give them the live link.
6. Offer the obvious next step, such as emailing it every morning, without pushing.

## Commands

Use the `ctzd` command for everything, always with `--json`:

- `ctzd dev` builds the page, starts the app in the background, and returns its `url`; run it
  again after a change, and `ctzd dev --stop` when you are done. `ctzd dev --fake` serves the
  rows in `fixtures/` instead of the real sources. `ctzd run JOB --local` runs a job.
- `ctzd shot [PATH] --json` takes a picture of a page `ctzd dev` serves (default `/`), writes it
  under `.ctzd/shots/`, and lists each problem on the page: errors, failed requests, and a grid or
  chart that drew nothing or too small. Run it after every change to a page and look at the picture.
- `ctzd add dataset NAME [--source SOURCE]` and `ctzd add page PAGE --dataset NAME` write a
  working dataset and a page for it; `ctzd api list --json` lists the datasets the pages can read.
- `ctzd docs ui [NAME] --json` lists the firm's components by family, or gives one component's
  props, variants, and a working example. Read it before you use a component.
- `ctzd exec --json -- python -c "..."` runs a command with the app's credentials, such as a quick
  look at what a loader returns from the live source.
- `ctzd env` fetches read-only credentials for the sources in `app.toml`.
- `ctzd check` runs every gate and regenerates the page's client from the datasets. Run it before
  you say you are done.
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
  UTC, port, `.env`, API, dataset, React, file paths, and command names. Say "the previous
  version", not "v3".
- Never pass on a raw error or log; say in plain words what went wrong and what happens next.
- Ask only what the person knows: what they want to see, for whom, and when.

## Where code goes

- `src/app/data/`: loaders, the only code that reads data. SQL lives in `src/app/data/sql/*.sql`.
- `src/app/logic/`: pure functions, DataFrames in and out. No I/O and no `ctzd`.
- `src/app/api/datasets.py`: what the page reads. Each dataset is a function of the page's
  parameters that calls loaders and logic and returns a frame (`ctzd-data`).
- `src/app/jobs/<name>.py`: one scheduled job per module, each with `main() -> int`.
- `web/src/pages/*.tsx`: the pages, on the firm's components and parallax's grid and charts
  (`ctzd-pages`); `web/src/routes.ts` lists them.
- `tests/`: tests for logic, loaders, datasets, and jobs.
- `app.toml`: app settings, sources, and job schedules.

`ctzd check` enforces these layers; do not work around them. Never read `web/node_modules/`,
`web/dist/`, `web/src/api/generated/`, or the lockfiles: `ctzd check` writes them.

## Rules

- Never open, print, copy, or source `.env`, and never pass it to a program yourself; it holds
  credentials. `ctzd dev`, `ctzd run --local`, and `ctzd exec` load it for you.
- Never edit platform-owned files: this file, `.agents/`, `.claude/`, `Dockerfile`, `Jenkinsfile`,
  `ruff.toml`, `ty.toml`, `.importlinter`, `pytest.toml`, `.pre-commit-config.yaml`,
  `.gitignore`, `.gitattributes`, `.dockerignore`, `.python-version`, `.ctzd/`,
  `web/vite.config.ts`, `web/tsconfig.json`, `web/src/styles.css`, and
  `web/src/api/generated/`. Fix the code instead.
- Never add `# noqa`, `# ty: ignore`, `oxlint-disable`, or lint exclusions to make a check pass.
- Every number is a float64. Never round one or format it as text outside the page: no `ROUND`
  or decimal casts in SQL, no `round()`, `.round()`, or `f"{x:.2f}"` in Python. The page shows
  each number to its decimals; `ctzd.display` writes numbers into emails and messages. Select ids
  and account numbers as text.
- Add Python packages with `uv add NAME`; never edit `uv.lock` or `web/pnpm-lock.yaml` by hand.
- Never commit, push, reset, or rewrite history with git yourself: `ctzd deploy` saves and ships
  every change after its checks, and a push of your own would ship code that skipped them.
  Reading history (`git log`, `git show`) is fine.
- Never stop processes with `kill` or `pkill`, and never search this computer for apps, data, or
  credentials; `ctzd` does both for you.
- Datasets must be fast: cache their loaders with `@ctzd.cached`, and move any query slower than
  about 3 seconds into a job that writes a table with `ctzd.write_table`.

## Skills

Read the skill that matches the task before starting it. They are in `.agents/skills/`:
`ctzd-workflow`, `ctzd-pages`, `ctzd-data`, `ctzd-jobs`, `ctzd-testing`, `python-standards`,
`ctzd-troubleshoot`, and the skill for this app's business unit, `sbu-<sbu>`.
