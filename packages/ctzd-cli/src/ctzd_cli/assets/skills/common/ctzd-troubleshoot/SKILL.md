---
name: ctzd-troubleshoot
description: "Diagnose and fix ctzd failures: each step of ctzd check in order, failed deploys and dev rollouts, a broken live dashboard, failed jobs, missing credentials and sample rows, approvals that wait, logins and an outdated ctzd. Use whenever a ctzd command returns ok false, or the person says the app or a job is wrong."
---

# Troubleshooting ctzd

Start from the failed command's `summary` and `next`: a failure names its step and gives the fix. Fix the cause
yourself, then tell the person in one plain sentence what happened and what changes for them. Never pass on a raw
error, and never ask them to run anything.

## `ctzd check`, step by step

The steps run in this order; each has a `name`, a `state` (`passed`, `fixed`, `failed`, `skipped`) and a `detail`.

| Step | Failure means | Fix |
|---|---|---|
| `platform files` | `fixed`: the locked files were put back or moved to this ctzd's version; nothing to do. `failed`: this ctzd is older than the app's `.ctzd/version`. | `uv tool upgrade ctzd`, then the same command again. |
| `environment` | `uv sync` failed; `detail` is uv's error. | A package or version the internal index lacks: `uv add` or `uv remove` it. A network error: retry. |
| `kit` | The installed kit is not the department's, or `.importlinter` was rewritten from it (`fixed`). | Keep the one kit `ctzd new` put in `pyproject.toml`. |
| `format` | The formatter could not parse a file. | The syntax error at the file and line in `detail`. |
| `lint` | ruff findings it could not fix: `CODE message --> file:line`. | Fix each one (`python-standards`). Never add an ignore. |
| `types` | ty findings: `file:line:col: error[rule] message`. | Fix the types (`python-standards`). |
| `layers` | `app.X is not allowed to import Y`. | Move the code to its layer (AGENTS.md, "Layout"): a read into `data/`, a rule into `logic/`. |
| `sql` | `src/app/data/sql/NAME.sql` does not parse in its source's dialect. | One statement per file; binds as `:name` (Oracle) or `%(name)s` (SQL Server); no templating. |
| `numbers` | Python or SQL that rounds or formats a number: `file:line: what`. | Remove it: a page names a `ui.py` format, a message uses `ctzd_common.display`. |
| `app.toml` | The sbu or name differ from the repository's, a job has no module or no `main()`, a schedule or time zone is invalid, or `[sources].use` names a source the kit lacks. | Put back the name it was made with; write `src/app/jobs/NAME.py` with `def main() -> int:`; `ctzd sources` for the names. |
| `tests` | The last lines of pytest. | Fix the test or the code (`ctzd-testing`). "Required test coverage of 70% not reached": add tests that assert real behaviour for the lines it lists in `src/app/logic/`. |
| `pages` | Driving the dashboard on its sample rows found a page that does not build, a callback wired to an id or property its page lacks, a read outside `ui.load`, or a callback that failed, held the server or answered too much. | The `ctzd-pages` table "When a check fails on a page"; `ctzd render PAGE --json` shows the failure. |
| `jobs` | A job's `main()` failed or returned non-zero on the sample rows. | `ctzd run JOB --local --json` shows its output. |
| `sample` | The template's sample page or job is still in place (a warning here; a failure in CI and for `ctzd deploy`). | Replace it with what the person asked for and delete its `ctzd: sample` first line. |
| `kit version` | A newer major version of the kit was tried, and the app moved to it (`fixed`) or stayed (a warning naming what failed). | Nothing, or fix what failed on the new version when there is time. |

## Deploys and dev

`ctzd deploy` follows CI after it opens the pull request. When it fails, nothing anyone sees has changed: dev keeps
what it ran before (a failed rollout is rolled back by itself), and prod is untouched. The result names the stage
and, for a rollout, the pods' last log lines and events. By stage:

- **check**: the same gates as `ctzd check`, strict: nothing is fixed, and `uv.lock` must match `pyproject.toml`.
  Run `ctzd check --json` here, then deploy again.
- **build**: a package failed to install in the image; read uv's error.
- **rollout**: the new version never became ready. Usually an import error, or a read that runs when a module is
  imported: keep every read inside a function.
- **smoke test**: a page path, a page's load or one of its callbacks answered an error on dev, or a job's `main()`
  failed on its sample rows there. Reproduce with `ctzd env`, `ctzd dev` and `ctzd render PAGE --json`; a common
  cause is a table a page reads that no job has written in dev yet (`ctzd-jobs`).

A failure that has nothing to do with the app (the registry or the cluster briefly unavailable) needs no change:
deploy again.

## The live app

| Symptom | Do |
|---|---|
| The person says prod is wrong | `ctzd rollback --json` first, then find the cause (`ctzd-workflow`). |
| Dashboard error, blank or slow | `ctzd logs --json` (prod) or `ctzd logs --env dev --json`: the traceback, and `loaded NAME in N ms` lines for slow reads. A slow read moves into a job that writes a table. |
| A job failed (the owners got an email) | `ctzd status --json` for its last run and Airflow link; `ctzd run JOB --local --json` to reproduce; fix, deploy, promote, then `ctzd run JOB --env prod --wait --json`. |
| `ctzd promote` or `ctzd delete` waits | Nobody has approved yet: give the person the approval link again from `ctzd status --json`. The person who promotes cannot approve it. |
| The pull request waits | It needs a colleague's approval, and a fresh one after every new change: send the review link again. |

## Data and credentials

| Message | Do |
|---|---|
| The page shows the sample-data banner on real data | A source has no credentials here: `ctzd env --json`, then `ctzd dev --json`. If it still shows, dev has no copy of that source, or it cannot run on a desktop (`ctzd sources --json` says which): tell the person the figures are sample rows here and real in prod. |
| `has no credentials in prod` | The platform team must add the source's secret to the prod Key Vault: tell the person. |
| `The ... kit has no source 'x'` | Use a name `ctzd sources` lists. Only the department's kit maintainers add sources: tell the person to ask them. |
| `has no sample rows for NAME` | Write `fixtures/SOURCE/NAME.csv` for your own SQL file `NAME.sql` (`ctzd-data`). |
| `is a ... source, which runs no SQL` | Read that source through its loaders. |
| `... hold numbers; an id is text` | Select the id as text in the SQL (`ctzd-data`). |
| `Table 'x' has never been written here` | Run the job that writes it: `ctzd run JOB --local` here, `ctzd run JOB --env dev` in dev. |
| `Refusing to publish an empty 'x'` | The read found no rows: find out why. `allow_empty=True` only when no rows is a real answer. |
| `ctzd dev` stopped right away | `output` holds the server's last lines: usually an import error in a page. |
| `Port N is in use` | Leave out `--port`. |

## Access and versions

| Message | Do |
|---|---|
| not logged in to GitHub, or the login was refused | The login in `ctzd-workflow`. |
| no access to the app | The app's owner or a colleague in the department must add the person; tell them. |
| `This folder is not inside a ctzd app` | Run from the app folder, the one with `app.toml`. |
| `next` says to run `uv tool upgrade ctzd` | Run it, then the same command again. |
| A network request failed | Retry in a minute; if it keeps failing, tell the person the platform cannot be reached. |

**A colleague changed the app too.** When `ctzd deploy` says the app's branch moved under you, its `next` says how
to bring both changes together; follow it, keep both changes, run `ctzd check --json`, then deploy again. Tell the
person a colleague changed the app too and both changes are kept.
