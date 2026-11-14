# ctzd rebuild plan

Agreed on 2026-11-13: drafted, reviewed against the code, and then settled decision by decision in a
design interview the same day. This plan replaces `docs/plan.md`. The first step of the work deletes
that file and points `AGENTS.md` and `README.md` here. Hostnames, organisations and people are `contoso`
placeholders.

ctzd lets business users with no coding experience build dashboards and scheduled jobs by talking to an
AI coding agent, and get back a link to the running app. The agent writes the code, runs the checks and
ships through the `ctzd` command line. The person never touches git, a Dockerfile or a terminal.

## 1. What changes

| Before | After |
|---|---|
| `ctzd-core`, a runtime library every app imports as `ctzd` | Gone. Apps import their department's kit, and `ctzd-common` for email and Slack. What kits share is `ctzd-kit`, which only kits import. |
| `ctzd-api`, a platform API between the CLI and GitHub, Jenkins, the cluster and Airflow | Gone. The CLI calls GitHub with the person's token. Everything that needs Azure or cluster access runs in GitHub Actions. |
| A React app on basis-ui with a basis-api backend | A Dash app in Python. No Node, npm or TypeScript anywhere. |
| Starters for React, jobs and common code, plus generators (`ctzd add dataset/page/job`, `ctzd api list`) | Two templates, **dash** and **job**, forked from basis's examples and the same for every department. No generators for apps. |
| Jenkins: an organisation folder, a shared library, rollback and delete jobs | GitHub Actions: one reusable workflow in this repo, called by a locked workflow in every app, on the platform's own runners |
| GitHub Enterprise | GitHub Enterprise Cloud, on github.com, called directly (a test organisation while testing here), so the whole GitHub side runs for real |
| One cluster, and a push to `main` is production | Two AKS clusters, **dev** and **prod**. A pull request deploys to dev, a second person approves it, and `ctzd promote` sends the release to prod after a second approval. |
| Platform releases served by the API from `platform-release/<n>` tags, on pilot and general channels | The templates, build files and skills ship inside the CLI. An app records the CLI version that last synced it, and CI checks the app with that version. |
| `ctzd shot`, `ctzd docs ui` | Gone. `ctzd render` shows the agent what a page holds, as JSON, with no browser. |
| `ctzd sources`, `ctzd exec` and `ctzd logs`, served by the API | Kept. The first two work through the kit, and `ctzd logs` through the workflow. |

What stays: the CLI as the agent's only tool, one repo per app, `app.toml`, locked build files with a
drift check, every gate of `ctzd check` that applies to Python, the numbers rule, Airflow 3 with the
factory DAG, the Helm chart, ACR, Key Vault, the Windows installer, the bootstrap skill and the
validation approach.

## 2. What a citizen developer experiences

1. They ask their agent for a dashboard or a job. The bootstrap skill, which the firm's skills platform
   gives every agent, has the agent run `ctzd setup --json` and follow `next`. That installs what is
   missing and runs `ctzd login`: a browser tab on github.com, the firm's single sign-on, a short code
   and one **Authorize** click.
2. `ctzd new bond-prices --sbu fi --kind dash` creates the GitHub repo, writes the template, pushes it,
   and installs it locally.
3. The agent changes the code. `ctzd sources` shows it what data the department's kit offers.
   `ctzd dev --fake` runs the dashboard on sample rows, and `ctzd render` shows the agent what each page
   holds. `ctzd env` followed by `ctzd dev` runs it on real data; that is the only time the person signs
   in to Azure, with their ordinary work account. `ctzd check` runs every gate.
4. `ctzd deploy` opens a pull request. CI checks it and puts it on the app's dev address. The agent
   gives the person two links: the dev address, and the pull request to send to a colleague.
5. The colleague opens the dev address and approves on GitHub. The change merges by itself and becomes
   a release.
6. `ctzd promote` asks for the release to go to prod. A colleague, who may be the same one, approves
   that on GitHub too. Prod goes live, and the app's owners get an email with the dashboard's address
   and each job's Airflow link.
7. `ctzd status` shows the release live in each environment, the jobs and their links, and the last
   run. `ctzd rollback` puts an earlier release back on prod at once.

## 3. Architecture

```text
 desktop (Citrix, Windows)                github.com                         Azure
 ┌──────────────────────┐ pull request  ┌───────────────────────┐  OIDC   ┌──────────────────────────┐
 │ agent ── ctzd CLI ───┼──────────────▶│ app repo              │────────▶│ ACR  contosoctzd         │
 │   templates, skills  │   REST API    │  .github/workflows/   │         │ AKS  ctzd-dev, ctzd-prod │
 │   (bundled)          │◀──────────────┤   ctzd.yml (locked)   │         │ Key Vault dev, prod      │
 └──────────────────────┘ status,       │      │ uses           │         │ ADLS (each kit's store)  │
                          deployments   │      ▼                │         │ Airflow in each cluster  │
                                        │ contoso/ctzd          │         └──────────────────────────┘
                                        │  .github/workflows/   │
                                        │   app.yml (reusable)  │──commits specs──▶ ctzd-dags repo
                                        └───────────────────────┘                (each Airflow reads it)
```

- **The CLI** does GitHub work with the person's token: it creates repos, opens pull requests, reads
  Actions runs, deployments and tags, and starts `workflow_dispatch` runs. Its only other outside call is
  `ctzd env`, which reads the dev Key Vault with the person's own Entra sign-in. With those credentials,
  `ctzd dev`, `ctzd sources` and `ctzd exec` reach the sources through the kit.
- **The reusable workflow** does everything that needs Azure, a cluster or Airflow. It signs in to Azure
  through OIDC, on self-hosted runners that only it may use. An app's own workflow is a few locked lines
  that call it at `v1`.
- **Kits** are the only code that reaches data. A kit is a department's package: a folder for each of
  its sources, its helper functions, and its own packages as dependencies (section 7). `kits/common`
  holds email and Slack.

## 4. Decisions

| Area | Decision | Reason |
|---|---|---|
| Users | Business users with no coding experience, on Citrix Windows desktops without admin rights, using any agent. Cursor is the default agent. The bar is the model people really use, Sonnet-class or better; Haiku runs are a stress test that informs and never blocks. Department engineers maintain the kits, and when one of them builds an app it takes the same path as everyone's. | That is the audience. Designing every rule for the weakest model would cost the design a lot and the strong models nothing. |
| Repos | One repo per app, made from one of two templates. A **dash** app is one dashboard plus any number of jobs. A **job** app is only jobs. | Keeps the job-plus-dashboard pair, which is the main speed tool, with one shape of repo. |
| UI | Dash on its FastAPI backend, served by uvicorn in the image and on the desktop. Pages use dash-mantine-components, dash-ag-grid and Plotly. Three locked modules, forked from basis-dash, give every page the firm's look and behaviour: `__main__.py` (the server, with its health and readiness routes), `theme.py` (the look) and `ui.py` (a grid's columns in the firm's number and date formats, the chart template, the loading, empty and failed states, the one handler of a callback's error, and `load`). | Python only. A locked module is source the agent can read, not a library, and it reaches every app through `ctzd sync`. Without `ui.py` every page would write its own grid formatters, which are JavaScript inside strings, and its own error states. |
| Async | A callback that reads data is `async` and awaits `ui.load`, which runs the kit's loader, a plain synchronous function, in a thread, with a limit for each source. `ctzd check` refuses a callback that reads any other way. Jobs stay synchronous. | On this backend a callback that is not `async` runs on the server's one loop, so a slow query in it stops every other request. With `ui.load`, maintainers and jobs never write async code. |
| Page checks | `ctzd check` drives a dash app on its fakes as a browser would, with no browser: every page builds, every callback's inputs, outputs and states name a component and a property its page has, and every callback a page load calls is called in the browser's order and must neither fail nor hold the server's loop. Forked from basis-dash's `testing.py`, `driving.py` and `checks.py`. The CLI ships the code and runs it inside the app's environment. | A component id is a string, so ruff and ty cannot see a callback wired to nothing, and a page whose callbacks all fail still builds its layout. |
| Seeing pages | `ctzd render PAGE [--set ID.PROP=VALUE]` prints what a page shows after its load, as JSON: each grid's columns and first rows as displayed, each chart's traces and axes, each text, and each callback that failed, with why. Forked from basis-dash's `render.py`. It is not a gate. | The agent's eyes, with no browser on the desktop. |
| Looking at data | `ctzd sources` lists the kit's sources and loaders, each with what it holds, its columns and an example call. `ctzd sources SOURCE [TABLE]` lists the tables of a source that has a driver, or a table's columns and first rows. `ctzd exec -- COMMAND` runs a command with the app's credentials. | In the trials, almost every broken guardrail was an agent opening `.env` to look at live data, because nothing else let it. Once `ctzd exec` existed, none did. |
| Templates | Forked from basis on 2026-11-13: the dash template from basis-dash's holdings example (positions page), and the job template from basis-job's daily positions example. Both are rewritten on plain Dash callbacks and pandas, and ctzd never syncs back from basis. From basis-dash ctzd takes the in-process testing, the page driving, the checks, `render`, the theme, the formats and the states. It does not take the declarative layer (`bd.App`, `bd.page`, datasets, resources). Apps do not depend on basis-dash, basis-job or basis-core. | No framework between the agent and Dash. A hard fork lets ctzd change the templates freely. |
| Generators | None for apps: `ctzd new` writes a template, and nothing else writes app code. Kit maintainers have one, `ctzd kit add source`. | The skills show how to add a page or a job, and `ctzd check` catches the mistakes. For maintainers the scaffold is the guidance: every source starts as the same working folder. |
| Data | Only through the department's kit. An app calls the kit's loaders, or runs its own SQL files through the kit's `query` on a source that has a driver. It reads and writes job tables through the kit's `store`. An app depends on exactly one kit, plus `ctzd-common`, and reads only that kit's sources. No app reads another department's sources in v1. | Each department owns its systems and their quirks. Agents never write a connection string. |
| Kits | A kit is a department's package, `ctzd-<sbu>`, in `kits/<sbu>` of this repo: one folder for each source, a folder of helper functions, and the department's own packages as dependencies. Every kit builds on `ctzd-kit`, which holds the loader seam, the fakes, the store and the drivers. A loader may use a driver, or call a department package that makes its own connections (section 7). | Departments bring datasets and utility packages, not UI. One wrapper and one fixture for each dataset is all the porting a package needs, while the two things the platform cannot work without, fakes and secrets from Key Vault, stay the same everywhere. |
| Versions | Python 3.14, pandas 3 and the dashboard's stack (Dash, dash-mantine-components, dash-ag-grid, Plotly) are the platform's, the same for every app. A kit picks the version of everything else it brings. Before the first real kit, three to five real department packages are run on this stack inside the firm. If a department's core packages cannot move, the fallback is an older Python first, and pandas 2.3 on Python 3.14 second. | One environment for agent-written code means one set of skills, templates and gates. pandas is the costly one to vary, because its major version changes how app code is written. A package that cannot move has two other ways through (section 7). |
| Common utilities | `ctzd_common.email` (SMTP relay, DataFrames attached as xlsx or csv) and `ctzd_common.slack` (one platform bot token, posting to a channel by name). A table sent with its column formats is written into the message as the page formats it. `ctzd_common.display` holds the display-rounding rule. Teams is dropped. | "Send me X every Monday" is the most common job, and the firm uses Slack. |
| Messages per environment | Locally, messages are saved under `.ctzd/outbox/` and never sent. In dev they go only to the app's owners, marked `[dev]`. In prod they go to whoever the code names. | A job deployed to dev must not email the desk a second time. |
| Numbers | Unchanged: every number is a float64 and only the display rounds. `ctzd check` refuses rounding in all of the app's Python and SQL. A page names one of `ui.py`'s formats for each column and axis, and never writes one. | One rule in one place. |
| Environments | Two AKS clusters, dev and prod, each with its own Key Vault, its own Airflow and its own store container. One ACR serves both. | Dev is where a change is proven on real infrastructure. Prod is what the business uses. |
| Dev data | Decided for each source. A source with an entry in the dev Key Vault is read for real, on the desktop and in the dev cluster. A source without one is served from fixtures there, and the page shows a sample-data banner. A table a job wrote from sample rows carries the same mark. A source may also declare that it cannot run on a desktop, which then always serves it from fixtures; that is a fallback to fix, and `ctzd kit check` warns about each one. | Good dev copies of the sources often do not exist. The department decides for each source by adding the entry or not, and a reviewer sees real numbers wherever a source allows. |
| Deploy | `ctzd deploy` runs `ctzd check`, puts the change on the app's branch, opens a pull request (or adds to the open one) with a description in plain words, and follows CI. Once the check passes, the pull request itself is built and deployed to dev, so the review and the dev rollout run side by side. An app has one open pull request at a time, because it has one dev address. | Every change is recorded and checked before `main`, and nothing is built on the desktop. Nothing waits on dev, and the reviewer sees the change running. |
| Review | A pull request merges when its checks are green and any second person has approved it on GitHub. New commits dismiss an approval. `ctzd deploy` turns on auto-merge, so nobody presses a button afterwards. The merge is a release, tagged `ctzd/vN`: the image dev already ran is recorded, not rebuilt. | A second pair of eyes on every change, at the cost of one interruption of one colleague. |
| Promote | `ctzd promote [--release N]` starts the workflow's `promote` action. It waits for a second approval on GitHub's page, from anyone but the person promoting, then deploys the release's existing image to prod and records a GitHub deployment. The same colleague may give both approvals. `ctzd delete` waits for the same approval. | Prod runs exactly the image dev ran. The first approval says the change is fine, and the second says release it now. |
| Rollback | `ctzd rollback [--release N]` puts back a release that was live on prod before, at once and with no approval. `run` and `logs` on prod need none either. Dev has no rollback: the next pull request replaces it. | A rollback returns to something already approved, and it must be instant when the live app is wrong. |
| State | Each environment's live release is a GitHub deployment in a GitHub environment on the app's repo, and each release is a `ctzd/vN` tag. `ctzd status` and `ctzd releases` read both with the person's token. | No ConfigMap and no API to read it through. |
| Failed rollouts | `helm upgrade --install --atomic` with readiness probes, then `ctzd ci smoke`. A rollout that fails either is rolled back. The run prints the pods' last log lines and events, and `ctzd deploy` returns them. | Users never see a broken rollout, and an agent can only fix a failed one if the evidence is still there when it looks. |
| Smoke test | Plain HTTP, no browser. Every page path, `/_dash-layout` and `/_dash-dependencies` must answer without a server error, and every callback a page load calls is posted to the live app, in the browser's order, and must not fail. For each job, a pod made as the factory DAG makes it runs `main()` once on fixtures, writing to a scratch place in the store and sending no message. | Dash serves its layout and callbacks as JSON, so HTTP can do what a browser's first load does. A job is proven only by a run, and fixtures always exist. |
| Logs | `ctzd logs [--env dev\|prod]` starts the workflow's `logs` action, which prints the dashboard pod's last log lines into the run, where the CLI reads them. A job's logs are in Airflow, and `ctzd run JOB --wait` returns a failed run's last lines. | The desktop has no cluster access, and there is no API to stream through. |
| CI identity | Actions signs in to Azure through OIDC as one managed identity per cluster. The apps organisation sets the token's subject to two claims, `job_workflow_ref` and `environment`, and `ctzd new` opts each repo in to that template. Each identity then trusts this repo's reusable workflow at `v1` in its environments: `dev` for the dev identity, `prod` and `prod-ops` for the prod one. No job signs in with a stored secret, and no runner holds an identity. The `check` job, which runs the app's own tests, gets no `id-token` permission and no secrets. The build job signs in only after the image is built. | A workflow a citizen writes cannot borrow the platform identity, and app code never runs next to credentials. The default subject names the repo, and a federated credential matches one exact subject with at most 20 to an identity, so the default would run out at the twentieth app. With this subject, three credentials cover every app. |
| Runners | Self-hosted, on the firm's network, in three groups of the apps organisation. `ctzd-check` is open to every app's workflows and reaches only the package index. `ctzd-dev` and `ctzd-prod` reach their cluster and its Airflow, and only this repo's reusable workflow may use them (each group is restricted to `contoso/ctzd/.github/workflows/app.yml@refs/heads/v1`). | The package index, the clusters and Airflow are not on the internet. App code runs only where there is nothing to reach. |
| App identity | The repository's name, `<sbu>-<app>`, is the app. `ctzd ci check` refuses an `app.toml` whose `sbu` and `name` say otherwise. | The host, the image, the Helm release, the store path and the job specs are all named from `app.toml`, so an edited name must never reach another app's. |
| Secrets | Each source's secret lives in each environment's Key Vault, put there by the platform team when a maintainer adds the source. It reaches an app the way the source says: as `CTZD_SOURCE_<NAME>` for a driver, or as the variables or the file a department package expects. In the cluster, External Secrets Operator syncs only the sources in `app.toml`'s `[sources].use` into the app's pods. On the desktop, `ctzd env` reads them from the dev Key Vault with the person's Entra sign-in and writes gitignored files. The platform's own credentials (the GitHub App's key for `ctzd-dags`, each Airflow's, and the mail relay's) are in the same Key Vaults, and the reusable workflow reads them once it has signed in. | One store per environment, the same read-only boundary as before (any member of the apps group can read dev credentials), and nothing in GitHub: any workflow in a repo can read an organisation secret the repo may use, so the platform keeps none. A package that reads its own variable or file works unchanged. |
| Images | `contosoctzd.azurecr.io/apps/<sbu>-<app>:<tree>`, tagged by the hash of the commit's tree, so a merge commit reuses its pull request's image. The last 20 per app are kept, which bounds rollback. One base image and one locked `Dockerfile` serve every app; a system package a department needs, such as a database client library, is added to the base image by pull request. | One image to patch and one build path, at the cost of a larger image. |
| Hosts | `<sbu>-<app>.ctzd-dev.contoso.com` and `<sbu>-<app>.ctzd.contoso.com`, each an `HTTPRoute` on its cluster's Envoy Gateway behind a wildcard certificate | Unchanged, apart from the second cluster |
| Jobs | Airflow 3, already running and liked for its UI, one in each cluster. The factory DAG reads `specs/<env>/<sbu>/<app>.json` from the `ctzd-dags` repo. The workflow commits a spec only after its environment's rollout passes the smoke test, so a job never runs an image that did not go live. Schedules run in prod only: in dev a job has no schedule, and runs when `ctzd run JOB --env dev` starts it. | People already use the Airflow UI to check on their jobs. CronJobs would need us to build that visibility. A schedule in both environments would run every query twice. |
| Running a job | `ctzd run JOB --local` runs it on the desktop. `ctzd run JOB [--env dev\|prod]` starts the workflow's `run` action, which triggers the DAG through that Airflow's REST API and reports the result and the Airflow link. | Airflow credentials stay in Actions. |
| Platform releases | The templates, locked files and skills ship inside the CLI. `.ctzd/version` records the CLI version that last synced the app, and the reusable workflow runs `ctzd ci check` at exactly that version, so CI always checks an app with the CLI that wrote it. Every app's workflow calls the reusable workflow at one long-lived branch, `v1`, so the deploy steps, the chart and the smoke test are always the platform's current ones. A change that breaks the contract between an app and the workflow goes to a `v2` branch, which an app reaches through `ctzd sync`. An app's files move to a newer CLI only when someone works on it (`ctzd sync`, which every command runs). | One versioned thing instead of three (CLI, platform release, channel). An untouched app keeps passing its checks, and a fix to how the platform deploys still reaches it. One branch is also one name for Azure and the runner groups to trust, not one for each CLI version. |
| CLI upgrades and channels | The CLI never replaces itself. The internal index is the channel: a stable version is general, and a pre-release (`0.7.0rc1`) is pilot, which a desktop joins with `ctzd setup --channel pilot`. When a newer version of its channel is on the index, `new`, `open`, `sync` and `deploy` stop and tell the agent to run `uv tool upgrade ctzd`; with the index out of reach they carry on. A bad version is undone by yanking it and publishing the last good one under a higher number; CI can still install a yanked version by its exact number. | Skills now arrive with the CLI, and a bad skill reaches every agent at once. So desktops must stay current, and there must be a pilot and an undo. |
| Lifecycle | `ctzd delete` starts the workflow's `delete` action: after its approval, the app is uninstalled from both clusters, its specs are removed, and a record is written for the janitor, which removes its tables 30 days later. The repo is then archived, not deleted. | Archiving can be undone. |
| Repo access | The creator is admin, and every member of the apps organisation has write access to every app repo, so anyone can review. Only members of a department's team can create an app on its kit. An organisation ruleset protects every app's `main`: a change arrives by pull request with one approval and green checks, and `main` refuses force-pushes and deletion. Each repo has three environments: `dev` takes any branch; `prod` takes `main` and requires a reviewer other than the person asking; `prod-ops`, for `rollback`, `run` and `logs`, takes `main` and requires none. | Colleagues can take over an app and review each other's. These are guards against mistakes in a high-trust firm, not controls that hold against an app's admin; GitHub's audit log is the record. |
| Dashboard access | No sign-in on dashboards or the Airflow UI in v1. Sign-in comes at the Gateway (the chart's `auth` block, off by default) before rollout goes beyond the FI pilot, and the smoke test will need a way in then. App code never handles identity. | Internal network only. Sign-in can be switched on later without changing any app. |
| Finding an app | `ctzd deploy`, `ctzd promote` and `ctzd status` return the dashboard's address and each job's Airflow link, and the agent passes them on. Each prod release emails the app's owners the same links, and `ctzd new` puts the prod address on the repo's GitHub page. No app directory in v1. | The address is predictable, but a business user needs a place to find it again without the agent, and will search their mail. |
| Skill distribution | The firm's internal skills platform carries one bootstrap skill, `ctzd`, which says to run `ctzd setup --json` and follow `next`. Everything else arrives with the CLI or the kit. No MCP server. | The bootstrap skill almost never changes. Every agent runs a command, while each reads MCP settings from a different file. |
| Enforcement | Unchanged: `ctzd deploy` runs `ctzd check` first, and CI runs it again. A Claude Code managed setting denies reading `.env`, allows `ctzd` commands in both Windows shells, and asks for `ctzd delete` and `ctzd exec`. Cursor's team terminal allowlist carries the same commands. | A person who cannot judge a command is never asked to approve a `ctzd` one. The `.env` rule is a speed bump; the read-only accounts are the real limit. |
| Command output | Every command prints a short summary for a person, and JSON with `ok`, `summary` and `next` when its output is not a terminal or `--json` is given. A failure names its step and gives the fix. | Agents act on `next`. An agent's shell is not a terminal, so a model that forgets `--json` still gets it. |
| Quality bar | Unchanged for apps: ruff with every rule family named in the locked `ruff.toml`, ty with every rule as an error, import-linter layers, SQL parsing, the numbers gate, and pytest with 70% branch coverage of `logic/`. A kit's code is held to this repo's own rules. | Strict where the agent pays the cost |

## 5. This repository after the rebuild

| Path | What it is |
|---|---|
| `packages/ctzd-cli` | The CLI, and in `src/ctzd_cli/assets/` everything it writes into apps: `templates/dash`, `templates/job`, `locked/`, `guidance/` and `skills/` |
| `packages/ctzd-kit` | `ctzd-kit`, what every kit builds on: the loader seam, the fakes, the store, the test helpers, the source explorer and the drivers |
| `kits/common` | `ctzd-common`: email, Slack and display rounding, and the dashboard's pins as its `dash` extra |
| `kits/fi`, `kits/mas` | Two made-up departments' kits. Between them they use every kind of source, and they are the worked examples for maintainers. Real kits exist only in the firm's fork |
| `.agents/skills/ctzd-kits` | The skill for a kit maintainer's agent: adding a source, wrapping a package, writing fixtures, running `ctzd kit check` |
| `.github/workflows/app.yml` | The reusable workflow every app calls |
| `.github/workflows/ci.yml` | This repo's own CI: the suites, `ctzd kit check` on every kit, e2e, actionlint, the Helm and Airflow tests, and each kit's release |
| `deploy/helm/ctzd-app` | The chart for one app: a Deployment and Service for a dash app, an `HTTPRoute`, `ExternalSecret`s, the job test pods, with values for dev and prod |
| `airflow/` | The factory and janitor DAGs, reading per-environment specs |
| `images/base` | The one base image: Python 3.14 and the system packages departments have asked for |
| `install/`, `bootstrap/skills/ctzd` | The Windows installer and the one bootstrap skill, updated for the new login and the absence of an API |
| `validation/` | The harness, with its API and Jenkins stages replaced (section 14) |
| `scripts/e2e.sh` | Generates one app of each template for each kit, offline, and runs `ctzd check` in each |

Removed: `packages/ctzd-core`, `packages/ctzd-api`, `jenkins/`, `docs/plan.md`, and everything React:
`locked-react/`, `starter/react/`, the `ctzd-pages` skill, `shot.py`, `node.py`, `generate.py`, the Vite
side of `devserver.py`, and the `[npm]` and `[ui.react]` tables. `api_models.py` shrinks to the models
the CLI still needs (`app.toml` and the job spec), and `api_client.py` becomes a thin GitHub client.

## 6. An app repository

```text
fi-bond-prices/
├── app.toml                    # the agent edits this
├── pyproject.toml              # the agent edits [project].dependencies only
├── uv.lock
├── README.md
├── src/app/
│   ├── __main__.py             # dash only: `python -m app` serves the app (locked)
│   ├── theme.py                # dash only: the firm's look (locked)
│   ├── ui.py                   # dash only: grid, chart, state and load helpers (locked)
│   ├── app.py                  # dash only: the Dash app, its shell and its pages
│   ├── pages/                  # dash only: one module per page, its layout and callbacks
│   ├── data/                   # the only I/O: calls into the kit, and sql/*.sql
│   ├── logic/                  # pure functions: frames in, frames out
│   └── jobs/<name>.py          # one module per job, each with main() -> int
├── tests/
├── fixtures/                   # sample rows for the app's own SQL; kit loaders bring theirs
├── .env.example                # written by ctzd sync from [sources].use
├── .github/workflows/ctzd.yml  # locked: calls contoso/ctzd's app.yml at v1
├── Dockerfile  ruff.toml  ty.toml  .importlinter  pytest.toml  .python-version   # locked
├── .pre-commit-config.yaml  .gitignore  .cursorignore  .gitattributes  .dockerignore  # locked
├── AGENTS.md  .agents/skills/  .claude/skills/   # guidance: written by ctzd, gitignored
└── .ctzd/version               # the CLI version that last synced the build files
```

A job app has the same layout without `__main__.py`, `theme.py`, `ui.py`, `app.py` and `pages/`.

```toml
[app]
name = "bond-prices"
sbu = "fi"
kind = "dash"             # dash | job
size = "s"                # s | m | l
owners = ["ann.lee@contoso.com"]

[sources]
use = ["fi_pricing"]

[[jobs]]
name = "sector_summary"
schedule = "0 7 * * 1-5"
timezone = "Europe/London"
size = "m"
retries = 1
timeout_minutes = 60
```

The import-linter layers. `.importlinter` is the one locked file written from the app's kit as well as
the CLI, because the kit says which of its modules do I/O.

- `pages` and `jobs` may import `data` and `logic`.
- `data` and `logic` never import each other or anything above them.
- Only `data` imports the kit's loaders, `query` and `store`.
- `logic` imports no I/O. It may import the kit's `helpers` and the department's pure packages by their
  own names.
- Nothing in the app imports `ctzd_kit`, or a package a source lists as doing I/O: an app reaches both
  only through its kit.
- `ctzd_common` may be imported from `jobs` and `pages`.
- `theme` and `ui` import nothing of the app's, and only `app.py` and `pages` import them.

## 7. Kits

A kit is how a department brings its data and its own packages to the platform. It is a Python package,
`ctzd-<sbu>`, kept in `kits/<sbu>` of this repo and maintained by the department's engineers. An app
depends on exactly one kit, and two kits are never installed together.

The platform owns one seam and the delivery of secrets. Everything behind the seam is the department's.

- **The seam is the loader.** An app reads data only by calling a kit loader, or by running its own SQL
  through the kit's `query`. With fakes on, a loader returns its sample rows and never runs what is
  underneath. That is what lets `ctzd check` run with no credentials and no network.
- **Secrets come from Key Vault.** The platform delivers a source's secret in whatever form the code
  underneath expects. How that code then connects is its own business.

### What a kit looks like

```text
kits/fi/
├── pyproject.toml              # ctzd-fi: depends on ctzd-kit and on FI's own packages
├── src/ctzd_fi/
│   ├── kit.toml                # the department: its code, title, GitHub team and code owners
│   ├── sources/
│   │   ├── fi_pricing/         # one folder per source, always these four things
│   │   │   ├── source.toml     # what it holds, its kind, how its secret arrives
│   │   │   ├── loaders.py      # the functions apps call
│   │   │   ├── sql/            # the loaders' SQL, if any
│   │   │   └── fixtures/       # sample rows for each loader
│   │   └── fi_risk/
│   ├── helpers/                # pure utility functions, which apps may import in logic/
│   └── NOTES.md                # the department's conventions, in its own words
└── tests/
```

```toml
# sources/fi_pricing/source.toml: a database, read with one of ctzd-kit's drivers
description = "Bond prices the pricing team publishes each morning"
refreshed = "each business day by 07:00 London"
kind = "oracle"
```

```toml
# sources/fi_risk/source.toml: a department package that makes its own connection
description = "Risk measures from the desk's own risk library"
refreshed = "each business day by 06:30 New York"
kind = "package"
io = ["fi_risklib"]             # apps may not import it; only this source's loaders do

[secret.env]                    # the variables the library already reads, from the secret's fields
FI_RISK_USER = "user"
FI_RISK_PASSWORD = "password"
```

```python
@loader
def prices(as_of: date) -> pd.DataFrame:
    """The clean price of every bond on a date."""
    return query("prices.sql", as_of=as_of)


@loader
def measures(as_of: date) -> pd.DataFrame:
    """DV01 and spread duration of every position on a date."""
    return fi_risklib.measures(as_of)
```

### Kinds of source

| Kind | What it reads | In version 1 |
|---|---|---|
| `oracle`, `mssql` | A database, through SQL files. Apps may also write their own SQL against it, and `ctzd sources` can look inside it | Yes, from `ctzd-core` |
| `rest` | An API, with paging, retries and OAuth | Yes, from `ctzd-core` |
| `file` | CSV, Excel or Parquet files from a folder or a blob container | Yes, new |
| `package` | Anything an internal Python package reads, with that package's own connection handling | Yes, new |
| `snowflake`, the data lake | | Added to `ctzd-kit` by the first department that needs one, in the same driver shape |

### Adding a source

1. `ctzd kit add source NAME --kind KIND` writes the folder with a working loader, a fixture and a test
   for that kind.
2. The maintainer fills in the description, the loaders and a few sample rows for each.
3. `ctzd kit check` proves the kit still meets the contract.
4. A pull request, approved by the kit's own code owner, merges it. CI releases the kit under its own
   version.
5. The platform team adds the source's secret to the dev Key Vault (if the department wants real data
   in dev) and the prod one.
6. Apps get the new source the next time someone works on them, through `ctzd sync`.

### The contract, version 1

1. **Shape.** The package is `ctzd-<sbu>`, builds on `ctzd-kit`, and passes this repo's lint, type and
   test rules.
2. **Installs from wheels.** On the desktop's Windows and the image's Linux, on Python 3.14 with pandas
   3, beside `ctzd-common[dash]`. Desktops have no compiler.
3. **Sources.** Every system the kit reads is a source folder with a description. Its secret arrives
   only the way `source.toml` says.
4. **Loaders and fakes.** Every read an app can make is a loader with sample rows. The kit's rows are
   the default fake everywhere, so an app that only calls loaders writes no fixtures; an app's own
   `fixtures/` overrides them.
5. **Network only inside loaders.** The contract check runs with the network blocked, so a helper that
   quietly calls a service fails there and must become a loader.
6. **Frames.** A loader hands the app a pandas DataFrame whose numbers are float64 and whose ids are
   text. What it uses inside is its own business.
7. **Guidance.** Every source has a description and every loader a docstring. The department's skill,
   `sbu-<sbu>`, is generated from them (each source, each loader, its columns, an example call) with
   `NOTES.md` added, so it cannot drift from the code. `ctzd sources` shows the same facts.

`ctzd kit check` proves all seven. It also makes an app of each template on the kit and runs
`ctzd check` on it. It warns about every source that declares it cannot run on a desktop.

The contract is frozen as version 1 only after one real department package has been ported inside the
firm. Until then the two made-up kits here keep it honest.

### Bringing existing packages

| The department has | What it does |
|---|---|
| A pure library: analytics, pricing, calendars | Lists it as a dependency. Apps import it by its own name, in `logic/` too. A typed wrapper in `helpers/` is worth writing where the library has no types. |
| A data library that reads the department's systems | Adds a `package` source for each system, with the secret in the form the library already reads, and one loader for each dataset. Nothing inside the library changes. |
| Only databases, APIs and files | Adds a source of that kind. The query function, the explorer and the fakes come from `ctzd-kit`. |

A package that cannot run on Python 3.14 and pandas 3 has two ways through that leave the platform's
stack alone:

- **Publish a table.** It keeps running where it runs today and writes its output somewhere a kit
  loader reads.
- **Its own interpreter.** A loader calls the package in a second environment and reads back a file.
  This is slow for each call, so it suits jobs better than live dashboard reads.

A package that only works on a Windows desktop (the person's own login, a desktop terminal's API, a
network share's path) cannot run in a Linux pod. It needs the first route, or a second way to log in
for the cluster.

### How kits are run

- **Rules.** A kit's code follows this repo's ruff, ty and test rules, enforced by CI. A kit's own code
  owners approve changes to it. The platform's owners approve `ctzd-kit`, `images/base` and everything
  else. A driver or helper that a second kit needs moves into `ctzd-kit`.
- **Releases.** Each kit has its own version and is released to the internal index when its pull
  request merges. A new department is a new folder and needs a CLI release, which carries the list of
  departments. A new source does not.
- **Reaching apps.** An app's `uv.lock` pins its kit, and CI installs exactly that. `ctzd sync` moves an
  app to the kit's newest release within the major version the app is on. It takes a new major version
  only if the app's checks pass on it, and otherwise stays and says so.
- **Boundaries.** An app reads only its own kit's sources, and its pods receive only the secrets of the
  ones in its `app.toml`. Only members of a department's GitHub team can create an app on its kit.
- **Real kits.** This repo is public, and a real kit names real sources and tables. Real kits are
  committed only to the firm's fork.

## 8. The templates

The **dash** template is the basis-dash holdings example cut to its positions page at `/`:

- `app.py` is a `dash.Dash` on the FastAPI backend with `use_pages`, inside a dash-mantine-components
  shell from `theme.py`.
- `pages/positions.py` has an as-of date filter, a dash-ag-grid grid and a Plotly chart, each built with
  `ui.py`'s helpers, so the page names its formats and writes none. Its callbacks are `async` and read
  through `ui.load`.
- `data/` shows both ways to read: one function calls a kit loader, and one runs the app's own SQL file
  through the kit's `query`.
- `logic/` holds what basis's `domain.py` computed.
- Tests cover `logic/`, plus each page's callback called on the fakes.

The **job** template is the basis-job daily positions example as a plain module:

- `jobs/daily_positions.py` has `main() -> int`. It loads trades and accounts through
  `data/`, runs the pure transform and checks from `logic/`, and writes `daily_positions` and
  `account_activity` with the kit's `store`.
- It shows one optional `ctzd_common.email` call.
- Tests cover the transform, the checks and `main()` on the fakes.

basis-job's framework parts (declared inputs and outputs, write modes, intervals, backfill and
replays) are left out. A job that needs one of them is written plainly, and the `ctzd-jobs` skill shows
how.

Both templates are the same for every department, with only the kit's name filled in. Their sample
reads `sample`, a source that `ctzd-kit` gives every kit and that exists only as sample rows.

Both templates carry the starter marker: `ctzd deploy` refuses an app whose sample page or sample job is
still in place.

## 9. The ctzd command line

| Command | What it does |
|---|---|
| `setup`, `login` | Unchanged: install what is missing, and the GitHub device flow |
| `new NAME --sbu SBU --kind dash\|job` | Creates the repo in the apps organisation, sets its access, its three environments, its OIDC subject and its address on GitHub, writes the template for the department's kit, pushes, and runs `uv sync` |
| `open NAME --sbu SBU` | Clones an existing app |
| `dev [--fake] [--port] [--stop]` | Runs the dash app locally in the background. Without `--fake`, a source with no credentials in `.env` still serves sample rows |
| `render PAGE [--set ID.PROP=VALUE]` | Prints what a page shows after its load, as JSON, on the fakes |
| `env` | Writes the app's sources' dev credentials from Key Vault, for the sources dev has |
| `sources [SOURCE [TABLE]]` | Lists the kit's sources and loaders, a source's tables, or a table's columns and first rows |
| `exec -- COMMAND` | Runs a command with the app's credentials |
| `check` | Every gate (section 10) |
| `sync` | Rewrites the build files and guidance from this CLI's bundle and the app's kit, and moves the app to the kit's newest release |
| `deploy` | Checks, opens or updates the pull request, follows CI and the dev rollout, and prints the dev address, each job's Airflow link and the pull request to send for review |
| `promote [--release N]` | Asks for a release to go to prod, prints the approval link, and follows it |
| `rollback [--release N]` | Puts a release that was live before back on prod, at once |
| `run JOB [--local \| --env dev\|prod] [--wait]` | Runs a job on the desktop or in an environment's Airflow |
| `status`, `releases` | The live release in each environment, what is waiting for whose approval, the jobs, and the last runs |
| `logs [--env dev\|prod]` | The dashboard's last log lines in an environment |
| `delete` | After an approval, uninstalls everywhere and archives the repo |
| `ci check \| render \| smoke \| release` | The steps the reusable workflow runs |
| `kit new SBU`, `kit add source NAME --kind KIND`, `kit check` | For kit maintainers: start a kit, add a source to it, and prove it meets the contract (section 7) |

Every command prints a short summary for a person, and JSON with `ok`, `summary` and `next` when its
output is not a terminal or `--json` is given. A failure names its step and gives the fix.

## 10. Quality gates

`ctzd check` runs, in order:

1. the build-file drift check against `.ctzd/version` and the app's kit;
2. `uv sync` (`--locked` in CI);
3. `ruff format` and `ruff check --fix` (check-only in CI);
4. `ty check`;
5. `lint-imports`;
6. a parse of every `src/app/data/sql/*.sql` in its source's dialect;
7. the numbers gate;
8. the `app.toml` check: `sbu` and `name` match the repository's name, every job has a module with
   `main()`, every schedule is valid under croniter, and every source in `[sources].use` is one the kit
   has;
9. `pytest`, with 70% branch coverage of `src/app/logic` when there is any logic;
10. for a dash app, the app driven on its fakes with no browser: every page builds, every callback's ids
    name a component and a property its page has, every callback that reads data is `async` and reads
    through `ui.load`, and every callback a page load calls is called in the browser's order and neither
    fails nor holds the server's loop. A callback with a pattern-matching id, a clientside one, and one
    with `prevent_initial_call` is checked by its ids alone;
11. each job's `main()` run once on the fakes.

## 11. CI/CD on GitHub Actions

The app's `.github/workflows/ctzd.yml` (locked) runs on a pull request, on a push to `main`, and on
`workflow_dispatch` with an `action` input (`promote`, `rollback`, `run`, `logs` or `delete`). It calls
`contoso/ctzd/.github/workflows/app.yml@v1`. Runs on one app and environment are serialised with a
`concurrency` group, which replaces Jenkins' lock.

The jobs run on three groups of self-hosted runners. `ctzd-check` is open to every app's workflows and
reaches only the package index. `ctzd-dev` and `ctzd-prod` are restricted to the reusable workflow at
`v1` and sit where they can reach their cluster and its Airflow. A job in an environment signs in to
Azure through OIDC as that cluster's managed identity. Its token's subject is `job_workflow_ref` and
`environment`, which no workflow of an app's own can produce.

On a pull request:

1. **check** (on `ctzd-check`): `ctzd ci check`, run with the CLI version in `.ctzd/version`, with no
   `id-token` permission and no secrets.
2. **build** (on `ctzd-dev`, environment `dev`): build the image, then sign in to Azure and push
   `apps/<sbu>-<app>:<tree>` to ACR.
3. **deploy-dev** (on `ctzd-dev`, environment `dev`): `helm upgrade --install --atomic` with
   `ctzd ci render --env dev`, then `ctzd ci smoke`, which includes each job's run on fixtures. On
   failure it prints the pods' last log lines and events, runs `helm rollback` and stops. On success it
   commits the job specs to `ctzd-dags/specs/dev/` and records the dev deployment.

The review runs beside these. The organisation's ruleset lets the pull request merge once the check and
the dev rollout are green and a second person has approved.

On the push to `main` that the merge makes, **release** runs `ctzd ci release`, which tags `ctzd/vN`
and records the release. The merge commit's tree is the pull request's, so the image exists and dev
already runs it. If `main` has moved and the tree differs, the build and the dev rollout run again
first.

The dispatch actions:

- **promote** (on `ctzd-prod`, environment `prod`) waits for the environment's reviewer, then deploys
  the image of `ctzd/vN`, runs the smoke test, commits the prod specs, records the deployment and
  emails the owners the links.
- **rollback** (on `ctzd-prod`, environment `prod-ops`) does the same with no reviewer and no email,
  and only for a release that has a prod deployment on record.
- **run** (environment `dev` or `prod-ops`) triggers the job's DAG in that Airflow and waits for it. A
  failed run's last log lines go into the workflow run.
- **logs** (environment `dev` or `prod-ops`) prints the dashboard pod's last log lines.
- **delete** (environment `prod`) waits for the reviewer, uninstalls the app from both clusters, removes
  its specs and writes the deletion record.

The workflow commits to `ctzd-dags` with a token from the platform's GitHub App, reaches Airflow with a
credential for each environment, and sends mail through the relay. It reads all three from the
environment's Key Vault once it has signed in. The platform keeps no organisation secrets, because any
workflow in an app's repo could read them.

Each cluster has one `ctzd-apps` namespace. The sizes `s`, `m` and `l` are unchanged, and each
dashboard runs one replica.

## 12. Jobs on Airflow

The factory DAG is unchanged except for two things: each Airflow reads `specs/$CTZD_ENV/` from the same
`ctzd-dags` bundle, and in dev it gives a DAG no schedule. It generates `<sbu>__<app>__<job>` DAGs that
call the job module's `main()` in the app's image with KubernetesPodOperator and only the app's sources,
and it emails the owners when a run fails. The janitor DAG reads the deletion records for its
environment.

## 13. Agent guidance and skills

`AGENTS.md` and the common skills are written from the CLI's bundle, and the department's `sbu-<sbu>`
skill from the app's kit. All are gitignored, as before.

- `ctzd-pages` is rewritten for Dash: pages, `async` callbacks and `ui.load`, `ui.py`'s grid, chart and
  state helpers, and `ctzd render`.
- `ctzd-data` becomes "use your kit": call a loader, or write SQL for a source that has a driver, and
  let `ctzd sources` say which.
- `ctzd-workflow` covers the pull request and the two approvals. The agent says in plain words what to
  send to whom ("send this link to a colleague to look over"), and never asks the person to use GitHub
  beyond clicking a link.
- `ctzd-jobs`, `ctzd-testing`, `ctzd-troubleshoot` and `python-standards` are updated for the new
  commands and the two environments.
- `ctzd-kits`, in this repo and not in apps, is for a kit maintainer's agent.

## 14. Validation without Azure

There is no Azure access here, so the plan is to make everything else real and replace the Azure services
with local stand-ins.

| Real service | Here |
|---|---|
| GitHub repos, pull requests, Actions, environments, deployments, tags, `workflow_dispatch` | Real, on a github.com test organisation that is named only in the developer's local config, never in this repo |
| ACR | A `registry:2` container |
| AKS dev and prod | Two kind clusters, `ctzd-dev` and `ctzd-prod`, with Envoy Gateway |
| Key Vault and External Secrets Operator | External Secrets Operator with its fake provider |
| Airflow in each cluster | Airflow 3 in each kind cluster, reading the test `ctzd-dags` repo |
| ADLS | A local folder, through the kit's local store |
| Entra sign-in for `ctzd env` | The kit's fake credentials |

The reusable workflow takes a `target` (`azure` or `local`) from an organisation variable. Its jobs run
on self-hosted runners in both. With `local`, the runners are on this machine and reach the stand-ins,
and the platform's own credentials come from the runner's environment. The Azure-only steps
(`azure/login`, `az acr login`, `az aks get-credentials`, and the Key Vault reads) are linted with
actionlint and rendered, but never run. The token is real, though: stage 1 asks GitHub for one in a
deploy job and checks that its subject is the one the federated credentials will name. The test repos
are private, because a self-hosted runner must never run a public repo's code.

The stages:

- **Stage 0.** The suites, `ctzd kit check` on both kits, `scripts/e2e.sh` for both templates on each
  kit, actionlint, `helm lint` and `helm template` with dev and prod values, and the Airflow tests.
- **Stage 1.** The CLI end to end against real GitHub and the stand-ins: `new`, `deploy` through a
  merged pull request, `status`, `promote`, `run --env`, `rollback`, `releases` and `delete`, for one
  app of each kind. The two approvals need a second test account. Without one, the test organisation
  asks for none, and Phase 0 checks them.
- **Stage 2.** The agent trials, on the new templates, with Claude Code only, because there is no
  Cursor here. Sonnet is the bar and Haiku a stress test. They run twice: on fake data as soon as the
  templates exist (section 15, step 6), and end to end after stage 1. They also compare the common
  skills as they are with one skill that has reference files.

What this cannot validate, so Phase 0 inside the firm checks it (section 16).

## 15. Order of work

In this repo each step ends with the checks in `AGENTS.md` passing and a commit to `main`. In the firm's
fork, every change to the platform or to a kit needs a pull request, one approving reviewer and green
CI.

1. **Docs.** This plan becomes the record: delete `docs/plan.md`, and point `AGENTS.md` and `README.md`
   here.
2. **Ask and measure**, inside the firm and alongside the rest. Put section 17's first item to the
   GitHub administrators. Run the tests of three to five real department packages on Python 3.14 with
   pandas 3. The first shapes step 8, and the second can change the Versions decision before step 4 is
   finished.
3. **Remove.** `ctzd-core`, `ctzd-api`, React, the generators and Jenkins. Trim the CLI to what still
   applies, with its tests.
4. **Kits.** Build `ctzd-kit` from `ctzd-core`'s drivers, store, caching, source explorer and fakes: the
   loader seam, the `file` and `package` kinds, the delivery of secrets, and `ctzd kit new`,
   `kit add source` and `kit check`. Build the two made-up kits on it, the generated department skill
   and the maintainers' skill. Move the email and display code into `kits/common`, and add Slack and
   the per-environment message rule.
5. **Templates.** Fork and rewrite the dash and job templates, `__main__.py`, `theme.py` and `ui.py`, on
   the FastAPI backend. Fork basis-dash's testing, page driving, checks and `render` into the CLI, with
   `sources` and `exec` on the kit. Then update the locked files, `e2e.sh` and the skills.
6. **Trials on fake data.** Run the agent trials with Claude Code on apps made offline: `dev --fake`,
   `render` and `check`, with no GitHub and no cluster. Fix what they find in the templates, `ui.py` and
   the skills before building further.
7. **CLI on GitHub.** Rewrite `new`, `deploy` with its pull request, `promote`, `rollback`, `run`,
   `logs`, `status`, `releases` and `delete` against GitHub's API, and `env` against Key Vault. The
   GitHub and Key Vault gateways stay thin, with fakes for the tests.
8. **CI/CD.** Write `app.yml`, the app's `ctzd.yml`, the chart's dev and prod values, its
   `ExternalSecret`s and its job test pods, and the factory's per-environment specs.
9. **Validation.** Build the stand-ins and the self-hosted runners, run stages 0 and 1 and the trials end
   to end, and record the results in `docs/validation-plan.md`.
10. **Fork.** Once stage 1 passes, the firm hard-forks this repo. From then on the fork is the only
    place changes are made, and nothing real ever enters this one.

## 16. Rollout

| Phase | What happens | Exit |
|---|---|---|
| 0, inside the firm | The GitHub administrators' answers. The package measurement. One real department package ported as a kit source, which freezes contract version 1. The checks this repo cannot make, listed below. | A sample app of each kind goes from `ctzd new` to prod on the real platform |
| 1, the FI pilot | Three to five people in FI build real apps on FI's real kit. Skills and the kit are tuned from what goes wrong. | Pilot apps reach prod without the platform team's help |
| 2, the other departments | Each starts with the package measurement and its own kit. Sign-in at the Gateway is on before this phase. | |

Phase 0's checks:

- Azure accepting the workflow's token, through the three federated credentials;
- the runner groups' restriction to the reusable workflow, and the organisation's ruleset and
  environments with their reviewers;
- ACR push and AKS deploy from the firm's runners;
- External Secrets Operator against Key Vault, with a secret delivered as variables and as a file;
- `ctzd login` through the firm's single sign-on, and `ctzd env`'s Entra sign-in, on a Citrix desktop;
- the Envoy Gateway wildcard routes;
- the real databases, and the departments' real packages on a desktop and in a pod;
- the dev server's reload on Windows, which basis left unverified for this backend;
- Cursor, the agent most people will use: one hand-run session, from `ctzd new` to a release, scored
  with the trials' rubric.

## 17. Open items

- **For the GitHub administrators.** Production is GitHub Enterprise Cloud. These are theirs to give:
  an organisation for the apps, whose members all have write access to its repos; a team for each
  department; the ruleset on every app's `main`; three runner groups, two of them restricted to the
  reusable workflow, on runners that reach the package index, the clusters and Airflow; the
  organisation's OIDC subject template (`job_workflow_ref` and `environment`); and an OAuth app with
  the device flow on for `ctzd login`. If accounts are managed by the enterprise, also whether members
  may create repos in that organisation.
- **Test setup.** A github.com test organisation, an OAuth app for `ctzd login`'s device flow, a
  GitHub App for the `ctzd-dags` commits and, for the approvals, a second account are needed before
  stage 1. They go in the maintainer's local config. Restricting a runner group to one workflow may
  need a paid plan on the test organisation.
- **The platform's workflow as a required one.** An organisation ruleset can require a workflow from
  this repo on every app's pull request. That would hold even if an app's own `ctzd.yml` were edited.
  Look at it in step 8.
- **The first real kits.** FI's package stack and first sources, then MAS and equities. What they bring
  will show what version 1 of the contract lacks, and which of Snowflake and the data lake comes first.
- **Other departments' sources.** None in v1. When a real case appears, decide whether the owning
  department signs off, and where that is recorded.
- **Log history.** `ctzd logs` shows only what the pod still holds. Container Insights would keep more.
- **Seeing pages.** `ctzd render` gives the agent a page as JSON, not as a picture. If the trials show
  agents need more, the options are a screenshot again, or Dash's own MCP server (`enable_mcp`, from
  Dash 4.3) on `ctzd dev` only.
- **An app directory.** None in v1. If people cannot find each other's dashboards, a page listing each
  department's apps from GitHub is the next step.

## 18. Decided while building

Steps 1 to 5 were built on 2026-11-13. Where this plan was silent the builder decided, and where the code departs from
a sentence above, this section says so. Each is open to change.

- **kit.toml lives inside the package** (`src/ctzd_fi/kit.toml`, beside `NOTES.md`), not beside `pyproject.toml`, so
  an installed kit can be read by `ctzd sources`, the generated skill and the contract check.
- **ctzd-kit depends on ctzd-common**, for one module, `ctzd_common.samples`: the kit marks each source that served
  sample rows there, and a page's `ui.load` reads the mark to show the sample-data banner without importing ctzd_kit.
- **The limit for each source is in the kit's loader seam**, a semaphore of the source's `max_concurrent` (default 4,
  in `source.toml`), so it holds for jobs too; `ui.load` runs the read with anyio's default thread limiter.
- **Sample rows** are one CSV per loader, `sources/NAME/fixtures/LOADER.csv`, and for an app's own SQL
  `fixtures/SOURCE/FILE_STEM.csv` in the app. They ignore the loader's arguments: an as-of date does not filter them.
- **app.py builds the app with `ui.dash_app(TITLE)`**, a `dash.Dash` on the FastAPI backend with Dash Pages, every
  setting given explicitly, React 19 and `on_error`, then sets `theme.shell(TITLE)` as its layout.
- **The look** is basis-dash's tokens, grid theme and chart template, forked, with the desktop's own fonts (no font
  files are bundled) and the colour scheme following the person's system setting, with no switch.
- **A chart's axes and hover text** are formatted by Plotly in the browser from each axis's format, so a tie can round
  differently there in its last digit; grids, stats, emails and Slack use the one rule.
- **The models**: `api_models.py` is gone; app.toml's models are in `appconfig.py`, and the job spec's in `render.py`.
- **Departments** are listed in the CLI's `platform.toml` (`[sbus.<sbu>]`: name, kit requirement, GitHub team).
- **import-linter** treats the kit and ctzd_common as root packages, so the contracts can name `ctzd_fi.helpers`,
  `ctzd_fi.sources` and `ctzd_common.email`.
- **`ctzd check` runs the app's tests, its jobs and then its pages on the sample rows**, with one scratch store and
  outbox in `.ctzd/out/`, so a page that reads a job's table finds it; a job's run sends no message. Section 10 lists
  pages before jobs.
- **Kit releases reach an app in `ctzd check`**, not in every command: the environment step moves it to the kit's
  newest release within its major version, and once every gate passes the next major version is tried, at most once a
  day, and kept only when every gate passes on it. Both need the index, which every command would otherwise wait on.
- **Kits give apps `cached`** beside `query`, `store` and `sample`, so a slow read in `data/` can be cached.
- **The two locked modules carry the platform's only suppressions**: Dash's private opt-in to React 19 in `ui.py`, and
  DMC 3.0.0's mistyped `theme` argument in `theme.py`. `ctzd_common.email` keeps its name through ruff's
  `allowed-modules`.
- **Not built in steps 3 to 5**, as section 15 orders: the GitHub and Key Vault side. `ctzd new` without `--offline`
  and `ctzd run` without `--local` say so; `open`, `env`, `deploy`, `promote`, `rollback`, `logs`, `status`, `releases`
  and `delete` are not in the CLI until step 7.
