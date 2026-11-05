---
name: ctzd
description: "Set up ctzd on this computer, start a new ctzd app (a Streamlit or Dash dashboard, or scheduled jobs), or open an existing one. Use when the person mentions ctzd or wants a dashboard, report, or scheduled email built and run for them."
---

# ctzd: setup and starting an app

ctzd builds and runs Python dashboards and scheduled jobs for people who do
not code. You do every technical step with the `ctzd` command.

## Rules

- The person is not technical. Never ask them to run a command, open a
  terminal, edit a file, or use git. Explain results in plain words.
- Always pass `--json` and read `ok`, `summary`, and `next`; `next` says what
  to do.
- When the person must act (approve a login), tell them exactly what to click.

## Set up

1. Run `ctzd --version`. If `ctzd` is not found, install it. On Windows run
   `powershell -c "irm https://ctzd.contoso.com/install.ps1 | iex"` and follow
   what it prints. If `ctzd` is still not found afterwards, use the path the
   script printed, or start a new shell.
2. Run `ctzd setup --json`. `checks` lists `uv`, `git`, `login`,
   `claude code`, and `agent instructions`, each `ok`, `fixed`, `warning`, or
   `missing`, with a `detail` that says what to do. Setup installs a private
   git itself when none is found.
3. If `login` is missing, log in:
   - `ctzd login --start --json` returns `user_code` and `verification_uri`
     and opens a browser tab. Tell the person: "In the browser tab that just
     opened (or at VERIFICATION_URI), enter the code USER_CODE and click
     Authorize."
   - Then `ctzd login --wait --json`. It blocks until they approve, so give it
     a 15-minute timeout. It returns their `login` and `email`. If the code
     expired, start again.
4. Run `ctzd setup --json` again until `ok` is true.
5. An `agent instructions` warning names a `CLAUDE.md` in a parent folder that
   would hide the app's instructions from Claude Code. Explain this to the
   person and rename it only with their agreement.

## Start a new app

1. Ask what they want, in their words: what it shows or sends, from which
   data, for whom, and how often. One app is one dashboard plus any number of
   scheduled jobs that share its data.
2. Their business unit: `fi` (Fixed Income) is the one known today. If
   `ctzd new` says "Unknown business unit", its `next` lists the known ones.
3. The kind of app: `--ui streamlit` (default, for most dashboards),
   `--ui dash` (when they or their team ask for Dash), or `--ui none` (only
   scheduled jobs, such as a weekly email).
4. A short name: 3 to 30 lowercase letters, digits, or hyphens, starting with
   a letter, without the business unit (`bond-prices`, not `fi-bond-prices`).
   The dashboard will live at `https://SBU-NAME.ctzd.contoso.com`. Confirm the
   name with the person in one line.
5. Run `ctzd new NAME --sbu SBU --ui UI --json`. It creates the repository and
   the app folder under the current folder (`--dir PARENT` puts it elsewhere).
   It returns `path`, `repo`, and `url`; the starter app goes live at `url`
   within a few minutes.
6. Errors: "already exists and is not empty" means choose another name or
   `--dir`; "does not know the owner's email" means log in first or pass
   `--owner EMAIL`.

## Open an existing app

If they give a link such as `https://fi-bond-prices.ctzd.contoso.com`, the SBU
is `fi` and the name is `bond-prices`. Run `ctzd open NAME --sbu SBU --json`
(`--dir PARENT` to choose where). It returns `path` and `url`. "You do not have
access" means the app's owner must add them; tell the person.

## Then work inside the app

- Work from `path`: every app command finds the app from the current folder.
- Read `path/AGENTS.md` and the skills in `path/.agents/skills/`, starting with
  `ctzd-workflow`, `ctzd-layout`, and the `sbu-SBU` skill, before you write
  code. If you were started in a parent folder, your agent did not load them:
  read them by path.
- For later sessions, suggest the person starts their agent in the app folder,
  so it loads these instructions by itself.
