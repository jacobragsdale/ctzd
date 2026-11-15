# Bringing ctzd to the firm

What to do on the first day in the firm's environment, in order. It assumes the state of `main` on 2026-11-15:
PLAN.md section 15's steps 1 and 3 to 5 are built and proven on Linux and on a Windows stand-in behind a
TLS-inspecting proxy; the GitHub, Key Vault and cluster side (steps 7 to 9) is not. Kits are out of scope here: `fi`
and `mas` are made-up departments with sample rows only.

## What works and what does not, before you start

Works on a desktop, offline: `ctzd new --offline`, `ctzd dev` (an open page reloads itself after a save: about 1 s
on Linux, 2.5 s on the Windows stand-in),
`ctzd render`, `ctzd check` (every gate), `ctzd run JOB --local`, `ctzd exec`, `ctzd sources`, and `ctzd kit new`,
`kit add source` and `kit check` for maintainers. `ctzd ci site` writes the installer and the bootstrap skill.

Not built: `ctzd new` without `--offline`, `ctzd run` without `--local`, `ctzd login` against a real GitHub app,
and `env`, `open`, `deploy`, `promote`, `rollback`, `releases`, `status`, `logs` and `delete`. Each says it is not built
yet and what an agent should do meanwhile, so an agent at work will tell the person plainly that the app runs only on
their computer for now.

## 1. Keep the firm's settings out of this repository

This repository is public. Clone it, then put the firm's settings in a private copy (a private fork, or a private
repository with this one as `upstream`). Never push a firm host, organisation or person here.

## 2. Make the repository's own environment install from the firm's index

`uv.lock` records PyPI as every package's registry, so on a desktop that cannot reach pypi.org it must be locked again
against the firm's index, in the private copy:

```sh
export UV_SYSTEM_CERTS=1                                 # trust the firm's authority and an inspecting proxy
export UV_DEFAULT_INDEX=https://INDEX.EXAMPLE/simple     # the firm's PyPI mirror
uv lock && uv sync
(cd airflow && uv lock && uv sync)                       # its own project, only where Airflow's tests run
uv run playwright install chromium                       # or skip it and use Edge: E2E_BROWSER=msedge
```

If `uv lock` fails, the mirror lacks something; step 3 names it.

## 3. Check the index holds what apps need

```sh
uv run python scripts/index_check.py --index https://INDEX.EXAMPLE/simple
```

It resolves every third-party package an app, the CLI and the kits need, wheels only, Python 3.14, for the Windows
desktop and the Linux image, and names what the index lacks. [package-index.md](package-index.md) is the same list
from PyPI, with versions, to hand to whoever runs the mirror, and the site's downloads.

## 4. Set the platform's addresses

Two files, nothing else (`test_platform_addresses.py` fails if an address is typed in anywhere the CLI ships):

- `packages/ctzd-cli/src/ctzd_cli/platform.toml`: GitHub host, API URL and organisation for apps (the OAuth client id
  can stay a placeholder until step 7), the registry and base image, `site` (the platform's own site: install.ps1 and
  `/downloads`), each environment's apps domain, Airflow, Key Vault and store, and the departments.
- `packages/ctzd-cli/src/ctzd_cli/assets/build.toml`: `[[python.index]]`, the index every app installs from.

Then run the checks in AGENTS.md. In the private copy the kit checks need no `CTZD_PYTHON_INDEX`: the internal index
is the default now.

## 5. Prove it on a desktop

On Linux or WSL, `scripts/e2e.sh` makes an app of each template on each kit, offline, runs every gate, opens each
dashboard in a browser (`E2E_BROWSER=msedge` for the installed Edge) and proves the reload. On a Windows desktop,
`scripts/prove-windows.ps1` does the same from wheels, in Edge, and proves the reload and a syntax error's recovery
(see its header; `-Index` or `CTZD_PYTHON_INDEX` names the index if build.toml's is not reachable from there).
`scripts/prove-install.ps1` runs the installer as a brand-new local user against `scripts/site_standin.py`, a
stand-in for the site and index; on a firm desktop, run the real installer instead, as the bootstrap skill says.

## 6. The installer and the bootstrap skill

```sh
uv run ctzd ci site --out site/
```

writes `site/install.ps1`, to serve at the site's root, and `site/skills/ctzd/SKILL.md`, for the firm's skills
platform. The site also serves uv, the Python build and MinGit under `/downloads`
([package-index.md](package-index.md) lists the paths). The installer writes the user's `uv.toml` with the internal
index, the Python mirror and `system-certs = true`.

## 7. Try an agent

The kits are not on the firm's index (nothing publishes them before step 7), so an app installs them from the local
wheels the proofs build, with the proofs' settings. After step 5, `scripts/e2e.sh ~/ctzd-try` (or the Windows proof's
`ROOT`) holds the wheels, `platform.toml` and `home`. Make an app with those settings, and start the agent from a shell
that has them, so its `ctzd` uses them too:

```sh
export CTZD_PLATFORM_CONFIG=~/ctzd-try/platform.toml CTZD_HOME=~/ctzd-try/home PATH="$PWD/.venv/bin:$PATH"
ctzd new bond-dashboard --sbu fi --kind dash --offline --owner YOU@FIRM --dir ~/ctzd-try/apps
cd ~/ctzd-try/apps/bond-dashboard && claude      # or open the folder in Cursor from this shell
```

On Windows, set the same two variables (`$env:CTZD_PLATFORM_CONFIG`, `$env:CTZD_HOME`) to the proof's `ROOT` before
starting the agent; the proof installed `ctzd` as a uv tool. Then ask for something in business words, as in
[trials/2026-11-15-smoke.md](trials/2026-11-15-smoke.md), and check the agent's work with `ctzd check`,
`ctzd dev --fake` and `uv run python scripts/browser_check.py APP_DIR --out DIR`.

## What to watch for

- The Windows proof ran behind a TLS-inspecting proxy, not the firm's: an index or site with its own certificate
  needs that authority in the Windows store (uv's `system-certs` reads it).
- Defender or another scanner on a firm desktop may slow `uv sync` and the dev server's first start.
- Nothing has run against the firm's GitHub, Entra ID, Key Vault, registry or cluster.
