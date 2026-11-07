---
name: ctzd-troubleshoot
description: "Diagnose and fix ctzd failures: check steps, failed deploys (build.stage, build.log_tail), page errors after deploy, failed Airflow jobs, missing credentials, conflicting changes, paused auto-deploy, outdated ctzd. Use when a ctzd command returns ok false or the live app misbehaves."
---

# Troubleshooting ctzd

Start from the failed command's `summary` and `next`. Fix the cause yourself,
then tell the person in one plain sentence what happened and what changes for
them. Never pass on a raw error, and never ask them to run anything.

## `ctzd check` steps

| Step | Failure means | Fix |
|---|---|---|
| `platform files` | `fixed`: platform files were put back or moved to the current platform release; nothing to do, `ctzd deploy` commits them separately. `failed` with "needs ctzd X or newer": this ctzd is too old for the app's platform release. "not on this computer": the platform could not be reached to fetch the app's release. | Too old: `uv tool upgrade ctzd`, then run the command again. Not on this computer: check the network and `ctzd setup`, then retry. |
| `environment` | `uv sync` failed; `detail` is uv's error. | A package name or version that the internal index does not have: correct it with `uv add` or `uv remove`. A network error: retry. |
| `format` | The formatter could not parse a file. | The syntax error at the file and line in `detail`. |
| `lint` | ruff findings it could not fix: `CODE message --> file:line`. | Fix each one (`python-standards`). Never add an ignore. |
| `types` | ty findings: `file:line:col: error[rule] message`. | Fix the types (`python-standards`). |
| `layers` | `app.X is not allowed to import Y: app.X.mod -> Y (l.N)`. | Move the code to the right layer (`ctzd-layout`). |
| `sql` | `src/app/data/sql/x.sql: <parser message>`: the file does not parse as Oracle or SQL Server. | One statement per file, binds as `:name` or `%(name)s`, no templating. |
| `tests` | The last 80 lines of pytest. | Fix the failing test or the code. Any warning fails a test (`ChainedAssignmentError`, a deprecation): fix what warns. |

"FAIL Required test coverage of 70% not reached" means branches in
`src/app/logic/` lack tests; the report lists the missing lines per file. Add
tests that assert real behavior for them (`ctzd-testing`).

## Failed deploys

`ctzd deploy --json` and `ctzd status --wait --json` report `build.state:
"failed"`, the stage that failed in `build.stage`, the last 60 log lines in
`build.log_tail`, and the build page in `build.url`. Production still runs the
previous release. Read `log_tail` for the real cause; by what the stage does:

- **Checks.** The same gates as `ctzd check`, but strict: nothing is fixed
  automatically and `uv.lock` must match `pyproject.toml`. Run
  `ctzd check --json`, then deploy again.
- **Image build.** A package failed to install: read uv's error in `log_tail`.
- **Rollout.** The new version never became ready and was undone
  automatically. Usual causes: an import error, or a query, `ctzd.read_table`,
  or source lookup that runs when the module is imported. Keep reads inside
  functions. Reproduce with `ctzd env --json` and `ctzd dev --json`.
- **Smoke test.** The live page showed an error box, and the release was
  rolled back. `log_tail` holds `The dashboard shows errors: URL: TEXT`.
  Common causes: a table the page reads was never written in production (the
  page must show a message when its loader returns `None`, see `ctzd-data`;
  deploy, then `ctzd run JOB --wait --json`), or an exception on empty data. Check what a loader
  returns from the live source with `ctzd exec` (see `ctzd-data`). Reproduce locally with
  real data (`ctzd env`, `ctzd dev`, open every page), add a test for the case,
  fix it. `the page did not finish loading within N s` means the page never
  rendered: usually a slow query on page load (move it into a job) or code that
  never returns. Look at the screenshot, then `ctzd logs --json`. If neither
  shows a cause and nothing changed in how the page loads, deploy again once:
  the smoke test can time out when the platform is busy.

A failure that has nothing to do with the app, such as the image registry
being unavailable during **Image build**, needs no change: `ctzd deploy --json`
with nothing new builds again when the last build failed.

`screenshot` is the PNG the smoke test took, saved under `.ctzd/screenshots/`,
for a failed deploy as well as a successful one. Look at it: a page without an
error box can still be blank or wrong. The smoke test also fails when a request
behind the page answers with a server error, such as a Dash callback that raised.

`ok: true` with "The build is still running": run `ctzd status --wait --json`.

## The live app

| Symptom | Do |
|---|---|
| Dashboard error, blank, or slow | `ctzd logs --json` (`--tail 500` for more): look for the traceback and `Slow query` warnings (move those queries into a job, `ctzd-data`). |
| Job failed (the owners got an email) | `ctzd logs --job NAME --json` shows the latest run's log. Reproduce with `ctzd run NAME --local --json`, fix, deploy, then `ctzd run NAME --wait --json` to run it again now and see whether it succeeds. |
| `has no deployed job` | Deploy before `ctzd run NAME`. |
| `ctzd status` shows `paused: true` | A rollback paused auto-deploy: pushes do not deploy until `ctzd deploy --json`, which resumes it. Deploy once the fix is in. |
| `has no release before vN to roll back to` | Nothing older exists; fix forward and deploy. |

## Data and credentials

| Message | Do |
|---|---|
| `Source 'x' is not configured: CTZD_SOURCE_X is not set` | Locally: add `x` to `[sources].use` in `app.toml`, run `ctzd env --json`, restart `ctzd dev`. In the live app's logs: the platform team must add the source; tell the person. |
| `ctzd env`: `Source 'x' does not exist` | Only the platform team adds sources. Tell the person to ask their SBU champion; do not guess other names. |
| `is not a valid source description` | The source's stored settings are broken: a platform-team fix. Tell the person. |
| `is a rest source; call ctzd.rest` (or the reverse) | Use the other call for that source. |
| `ctzd.DataError: prices: missing column 'sector'` | The SQL and the loader disagree: fix the query's columns or the expected names. |
| `Table 'x' of app SBU/APP has never been written` | Run the job that writes it: `ctzd run JOB --local --json` here, `ctzd run JOB --json` live. |
| `Refusing to publish an empty 'x'` | The query found no rows: find out why. Pass `allow_empty=True` only when no rows is a real answer. |
| `is cached, so its arguments must be hashable` | Pass tuples, not lists or dicts. |
| `httpx.HTTPStatusError` 401 or 403 from a REST source | The source's account was refused: a platform-team fix. 404: check the path. |

## Access, versions, and conflicts

| Message | Do |
|---|---|
| `You are not logged in to GitHub` / `did not accept your GitHub login` | The two-step login in `ctzd-workflow`. |
| `You do not have access` | The app's owner or the SBU champion must add the person to the app's repository; tell them. |
| `This folder is not inside a ctzd app` | Run from the app folder (the one with `app.toml`). |
| `Platform release N needs ctzd X or newer` | `uv tool upgrade ctzd`, then run the same command again. Any command can say this when the platform moves on. |
| `next` says a newer ctzd is available | Optional: `uv tool upgrade ctzd` when the person is not waiting on you. |
| `The agent guidance could not be refreshed` | Carry on; `AGENTS.md` and these skills may be a little out of date until the platform is reachable again. |
| `The ctzd platform could not be reached` / `A network request failed` | Retry in a minute; if it keeps failing, tell the person the platform is unreachable. |

**"Someone else changed this app and their changes conflict with yours."**
`next` names the conflicting files. Your change is already saved locally and
the colleague's change already fetched, so no login is needed to merge them:

1. `git rebase origin/main`
2. Edit each conflicting file to keep both changes and remove the conflict markers.
3. `git add FILE...`, then `git -c core.editor=true rebase --continue`.
4. `ctzd check --json`, then `ctzd deploy --json`.

If `git` is not on the PATH, ctzd's private copy is
`%LOCALAPPDATA%\ctzd\mingit\cmd\git.exe`. If git asks who you are, add
`-c user.name="NAME" -c user.email="EMAIL"` with the login and email
`ctzd login --wait` reported. Tell the person a colleague changed the app too
and both changes are kept.
