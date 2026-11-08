# Starting with ctzd

`ctzd setup --json` returns this guide once the computer is ready. It comes
from the platform, so it is always current.

## Rules

- The person is not technical. Never ask them to run a command, open a
  terminal, edit a file, or use git. Explain results in plain words.
- Always pass `--json` and read `ok`, `summary`, and `next`; `next` says what
  to do.
- When the person must act (approve a login), tell them exactly what to click.
- Everything about apps and data comes through ctzd: `ctzd open` for an
  existing app, `ctzd sources --json` and the app's SBU skill for data. Never
  search this computer's files for apps, databases, or credentials: they are
  not on it, and the person's files are theirs.

## Logging in

If `ctzd setup` reports `login` as missing:

- `ctzd login --start --json` returns `user_code` and `verification_uri` and
  opens a browser tab. Tell the person: "In the browser tab that just opened
  (or at VERIFICATION_URI), enter the code USER_CODE and click Authorize."
- Then `ctzd login --wait --json`. It blocks until they approve, so give it a
  15-minute timeout. It returns their `login` and `email`. If the code expired,
  start again.
- Run `ctzd setup --json` again until `ok` is true.

An `agent instructions` warning names a `CLAUDE.md` in a parent folder that
would hide the app's instructions from Claude Code. Explain this to the person
and rename it only with their agreement.

## Start a new app

1. Ask what they want, in their words: what it shows or sends, for whom, and
   how often. One app is one dashboard plus any number of scheduled jobs that
   share its data. Do not ask where the data lives, which database or table, or
   how to connect: they do not know. Create the app first; its SBU skill lists
   the business unit's sources and tables, and `ctzd sources --json` lists every
   source by name.
2. Their business unit: `fi` (Fixed Income) is the one known today. If
   `ctzd new` says "Unknown business unit", its `next` lists the known ones.
3. The kind of app: `--ui react` (the default: a dashboard, with or without
   scheduled jobs), or `--ui none` (only scheduled jobs, such as a weekly email).
4. A short name: 3 to 30 lowercase letters, digits, or hyphens, starting with
   a letter, without the business unit (`bond-prices`, not `fi-bond-prices`).
   The dashboard will live at `https://SBU-NAME.ctzd.contoso.com`. Confirm the
   name with the person in one line.
5. Run `ctzd new NAME --sbu SBU --ui UI --json`. It creates the repository and
   the app folder under the current folder (`--dir PARENT` puts it elsewhere).
   It returns `path`, `repo`, and `url`; the starter app goes live at `url`
   within a few minutes. Never add `--offline`: it is for platform tests, and
   an app made with it can never be deployed.
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
  `ctzd-workflow`, `ctzd-pages`, and the `sbu-SBU` skill, before you write
  code. If you were started in a parent folder, your agent did not load them:
  read them by path.
- For later sessions, suggest the person starts their agent in the app folder,
  so it loads these instructions by itself.
