---
name: ctzd-workflow
description: "Run, check, ship, and roll back a ctzd app with the ctzd command, read its --json output, and log in. Use for any request to see, try, ship ('ship it', 'make it live'), check on, or undo a change to the app."
---

# ctzd workflow

## Rules

- The person is not technical. You run every command. Never ask them to run one,
  edit a file, open a terminal, or use git.
- Run `ctzd` from inside the app folder (the one with `app.toml`), always with
  `--json`. Read `ok`, `summary`, and `next` first: `next` says what to do.
  Exit status 0 is success, 1 is a failure, 2 is a wrong command line.
- Never open, print, or copy `.env`; `ctzd` loads it for you.
- Never run git yourself: `ctzd deploy` commits and pushes after the checks pass.
- A change is done when `ctzd check --json` passes. It is live only when
  `ctzd deploy --json` returned a `release`.

## The loop

| Step | Command | Notes |
|---|---|---|
| Sources | `ctzd sources --json` | Names of the data sources the platform has, for `[sources].use` in `app.toml`. |
| Credentials | `ctzd env --json` | Once per session, and after changing `[sources].use` in `app.toml`. Needed before anything reads real data. |
| Look | `ctzd dev --json` | Regenerates the page's client from the datasets, builds the page, starts the app in the background, and returns its `url` (`http://localhost:8501`, or the next free port). Run it again after every change to rebuild and restart it; `ctzd dev --stop --json` stops it. Never use `kill` or `pkill`. Give the person the link to open; `ok: false` names the `step` that failed and its `output`. |
| Look at live data | `ctzd exec --json -- python -c "..."` | Runs a command with the app's credentials, as `ctzd dev` does, and returns `exit_code` and `output`. For checking what a loader returns; never source `.env` yourself. |
| Try a job | `ctzd run JOB --local --json` | Runs `src/app/jobs/JOB.py` here. Emails and Teams posts land in `.ctzd/out/outbox/`, tables in `.ctzd/out/store/`; nothing is sent. |
| Check | `ctzd check --json` | Every gate. Fixes formatting and simple lint itself (`"state": "fixed"`). `--fast` skips tests while you iterate. |
| Ship | `ctzd deploy --json` | Checks again, saves and uploads the change, waits for the build. See below. |
| Live state | `ctzd status --json` | Live release, URL, job links, last build, pause flag. |
| Run a live job | `ctzd run JOB --wait --json` | Runs the deployed job in Airflow and waits for `state`: `success` or `failed`; give the person `url`. Without `--wait` it returns as soon as the run starts. |
| Undo | `ctzd rollback --json` | Restores the previous release in seconds. `ctzd releases --json` lists releases; `ctzd rollback v7 --json` picks one. |

## Deploying

`ctzd deploy` waits up to 540 s for the build (`--timeout SECONDS`), so give the
shell command a timeout of at least 10 minutes. If your tool cannot wait that
long, run `ctzd deploy --detach --json`, then `ctzd status --wait --json`, which
waits for the same build and returns the same fields. `--message "Add sector
filter"` labels the change; the default lists the changed files.

Read the result:

- `ok: true` with `release`: live. `url` is the dashboard and `dags` maps each
  job to its Airflow page. The platform opened the live page after the rollout,
  and a page with an error, a failed request, or a page that never finished
  loading would have been rolled back.
- `ok: true`, no `release`, `build.state` is `queued` or `running`: still
  building. Run `ctzd status --wait --json` again.
- `ok: false` with `steps`: a check failed and nothing was uploaded. Fix it and
  deploy again.
- `ok: false` with `build.state: "failed"`: the build stopped at `build.stage`,
  and `build.log_tail` holds its last log lines. Production still runs the
  previous release, so the
  person sees no change. Fix the cause, run
  `ctzd check`, deploy again. See `ctzd-troubleshoot`.

## When the person says the live app is broken

1. `ctzd rollback --json`. The previous release is back when `done` is true.
   Tell them it is fixed for now.
2. Auto-deploy is now paused (`ctzd status` shows `paused: true`): nothing
   reaches production until the next `ctzd deploy`.
3. Find the cause (`ctzd logs --json`, `ctzd-troubleshoot`), fix it, run
   `ctzd check --json`, then `ctzd deploy --json`, which resumes auto-deploy.

## Logging in

When a command says "You are not logged in" or "did not accept your GitHub login":

1. `ctzd login --start --json` returns `user_code` and `verification_uri` and
   opens a browser tab. Tell the person: "Open VERIFICATION_URI, enter the code
   USER_CODE, and click Authorize."
2. `ctzd login --wait --json` blocks until they approve (or `expires_at`
   passes), so give it a timeout of 15 minutes. It returns `login` and `email`.
   If the code expired, start again.

## JSON fields

Every result has `ok`, `command`, `summary`, and `next`, plus:

| Command | Fields |
|---|---|
| `check` | `steps`: list of `name`, `state` (`passed`, `fixed`, `failed`, `skipped`), `detail` |
| `deploy`, `status --wait` | `commit`, `build` (`number`, `state`, `stage`, `url`, `log_tail`, `release`); when live also `release`, `url`, `dags` |
| `deploy --detach` | `commit`, `committed` (false when there was nothing new) |
| `status` | `sbu`, `name`, `url`, `release`, `paused`, `dags`, `last_build` |
| `run JOB --local` | `exit_code`, `output` (last 40 lines) |
| `run JOB` | `dag_id`, `run_id`, `url` |
| `logs [--job JOB] [--tail N]` | `text` |
| `releases` | `releases`: list of `release`, `commit`, `image`, `created_at`, newest first |
| `rollback` | `release`, `done`, `url` |
| `env` | `variables` (names only) |
| `add job` | `job`, `files` |
| `dev` | `url`, `port`, `log` (the server's output file); on failure `output` |
| `dev --stop` | `stopped` |
| `sync` | `release`, `changed` |

A failure the command caught itself has only `ok: false`, `summary` (what went
wrong), and `next` (what to do).

## Telling the person

Say what changed for them, with the link, in the words of `AGENTS.md`'s
"Talking with the person": no commit, push, build, Jenkins, repository, lint,
tests, or release numbers.

| Result | Say |
|---|---|
| Release live | "Your dashboard is updated: URL." |
| Build failed | "The update did not go out because REASON. What people see is unchanged. I am fixing it now." |
| Built but not deployed (`summary` says nothing was deployed) | Nothing yet: follow `next`, then report the outcome. |
| Checks failed | Nothing yet: fix it, then report the outcome. |
| Rollback done | "I put the previous version back; it is live now. Next I will fix the change and send it again." |
| Job started | "The job is running. You can follow it here: URL." |

`ctzd delete --yes` removes the dashboard and jobs (tables go after 30 days).
Run it only when the person clearly asks, after confirming with them. `ctzd sync`
is rarely needed: `ctzd check` restores platform files itself.

The platform keeps this app's `AGENTS.md` and skills current on its own: any
`ctzd` command may rewrite them, and they are not part of the app's history.
When the platform's build files change, `ctzd deploy` commits them first, as
"Upgrade platform files to platform release N", then the person's change.
Neither needs explaining unless the person asks.
