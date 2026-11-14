# Instructions for AI agents

This is a ctzd app: one repository, made from the **dash** template (one dashboard, plus any number of jobs) or the
**job** template (only scheduled jobs). It is Python only: Dash pages on dash-mantine-components, dash-ag-grid and
Plotly, and jobs that Airflow runs. It reads data only through its department's kit.

The person you work with is not a programmer. Do every technical step yourself with the `ctzd` command, explain
results in plain words, and never ask them to run a command, open a terminal, edit a file or use git. The only thing
they ever do on GitHub is click a link you give them.

## Layout

```text
app.toml              the app: name, sbu, kind, owners, [sources].use, [[jobs]]   (you edit it)
pyproject.toml        edit [project].dependencies only, with `uv add NAME`
src/app/
  app.py              dash: the Dash app and its title
  pages/              dash: one module per page, its layout and its callbacks        (ctzd-pages)
  data/               the only I/O: calls into the kit, and sql/*.sql                (ctzd-data)
  logic/              pure functions: frames in, frames out
  jobs/NAME.py        one module per job, each with main() -> int                   (ctzd-jobs)
  __main__.py theme.py ui.py                                                         locked (dash)
tests/                tests of logic/, each page's callbacks and each job            (ctzd-testing)
fixtures/SOURCE/NAME.csv   made-up rows for the app's own SQL
```

The layers, which `ctzd check` enforces: `pages` and `jobs` import `data` and `logic`; `data` and `logic` never
import each other; only `data` imports the kit's sources, `query` and `store`; `logic` does no I/O, but may use the
kit's `helpers`; only `pages` and `jobs` send email or Slack (`ctzd_common`); only `app.py` and `pages` import `ui`
and `theme`. Nothing imports `ctzd_kit`.

## Rules

- **Data only through the kit.** Call one of its loaders, or run your own SQL file with its `query`, from `data/`.
  Never write a connection string or import a database driver. `ctzd sources` and the `sbu-<sbu>` skill say what the
  kit has; if nothing matches what the person asked for, tell them, never invent a source.
- **Numbers are never rounded.** Every number is a float64 from the source to the screen. A page names one of
  `ui.py`'s formats for each column, stat and axis; an email or a Slack message names a `ctzd_common.display`
  format. No `ROUND` or decimal casts in SQL, no `round()`, `.round()` or `f"{x:.2f}"` in Python: `ctzd check`
  refuses them. Ids are text.
- **Never open `.env`.** Never read, print, copy or source it, and never pass it to a program yourself: it holds
  credentials. To look at live data, run `ctzd exec --json -- python -c "..."`, which loads them for you.
- **Never edit locked files**: this file, `.agents/`, `.claude/`, `src/app/__main__.py`, `src/app/theme.py`,
  `src/app/ui.py`, `.github/`, `Dockerfile`, `ruff.toml`, `ty.toml`, `.importlinter`, `pytest.toml`,
  `.pre-commit-config.yaml`, `.gitignore`, `.cursorignore`, `.gitattributes`, `.dockerignore`, `.python-version`,
  `.env.example`, `.ctzd/`. ctzd puts them back. Read `ui.py`'s docstrings: they are the page API.
- Never add `# noqa`, `# type: ignore`, `# ty: ignore` or an exclusion to pass a check. Fix the code.
- Never run git to commit, push, branch or merge: `ctzd deploy` does it after the checks. Reading history is fine.
- Never stop processes with `kill`, and never search this computer for apps, data or credentials.
- The template's sample page or sample job (its first line says `ctzd: sample`) blocks a deploy: replace it with
  what the person asked for, and delete that line.

## The order of work

1. Ask at most three things, all about the business: what the page or job answers, who reads it and how often, which
   numbers matter. Never ask where the data lives.
2. `ctzd sources --json`: pick the source whose description matches, and tell the person in their words which data
   you will use ("the bond prices the pricing team publishes each morning"). Name it in `app.toml`'s `[sources].use`.
3. Write `data/`, `logic/`, then the page or job, then their tests.
4. `ctzd dev --fake --json` runs the dashboard on sample rows; `ctzd render PAGE --json` shows you what the page
   holds. `ctzd run JOB --local --json` runs a job. Fix until they show what was asked.
5. `ctzd env --json`, then `ctzd dev --json`, for real data. Give the person the `url` to look at, and check a
   number or two with them against what they see elsewhere.
6. `ctzd check --json` until it passes.
7. When they are happy, `ctzd deploy --json`. Give them the dev address to look at, and the review link: "send this
   link to a colleague to look over". After the colleague approves, `ctzd promote --json` takes it to prod.

## Commands

Always pass `--json`, and read `ok`, `summary` and `next`: `next` says what to do.

| Command | What it does |
|---|---|
| `ctzd sources [SOURCE [TABLE]]` | the kit's sources and loaders; a database source's tables; a table's columns and first rows |
| `ctzd dev [--fake] [--stop]` | runs the dashboard in the background and returns its `url`; run it again to restart |
| `ctzd render PAGE [--set ID.PROP=VALUE]` | what a page shows after its load, as JSON, on the sample rows |
| `ctzd run JOB --local` | runs a job here; messages are kept in `.ctzd/outbox/`, tables in `.ctzd/out/store/` |
| `ctzd env` | the dev credentials of the sources in `[sources].use` |
| `ctzd exec -- COMMAND` | runs a command with the app's credentials |
| `ctzd check [--fast]` | every gate; `--fast` skips the tests while you iterate |
| `ctzd deploy` | checks, opens or updates the pull request, puts it on the dev address |
| `ctzd promote`, `ctzd rollback` | sends a release to prod after a second approval; puts an earlier one back at once |
| `ctzd status`, `ctzd releases`, `ctzd logs`, `ctzd run JOB --env dev\|prod` | the live app |

## Skills

Read the one that matches the task before you start it. They are in `.agents/skills/` (and `.claude/skills/`):

| Skill | For |
|---|---|
| `ctzd-pages` | adding or changing a page: layout, callbacks, grids, charts, stats, `ctzd render` |
| `ctzd-data` | reading data: loaders, your own SQL, sample rows, `ctzd sources`, `ctzd exec`, the store |
| `sbu-<sbu>` | this department's sources, loaders, columns and conventions, written from its kit |
| `ctzd-jobs` | scheduled jobs, email and Slack |
| `ctzd-testing` | what to test and how |
| `ctzd-workflow` | checking, deploying, the two approvals, prod, rollback |
| `ctzd-troubleshoot` | any command that returns `ok: false`, or a live app that misbehaves |
| `python-standards` | any Python you write, and ruff or ty findings |

## Talking with the person

- Say what changed for them, with the link. Leave out words they do not use: commit, push, branch, pull request,
  merge, build, pipeline, repository, lint, tests, coverage, smoke test, cron, UTC, port, `.env`, callback, loader,
  file paths and command names. Say "the previous version", not "release 7".
- Never pass on a raw error or log: say in plain words what went wrong and what happens next.
- Ask only what the person knows: what they want to see, for whom, and when.
