# Starting with ctzd

`ctzd setup --json` returns this guide once the computer is ready. It ships inside ctzd, so it matches the ctzd
you are running.

## Rules

- The person is not technical. Never ask them to run a command, open a terminal, edit a file or use git. Explain
  results in plain words. The only thing they ever do on GitHub is sign in and click links you give them.
- Always pass `--json` and read `ok`, `summary` and `next`; `next` says what to do.
- When the person must act (approve a login, sign in), tell them exactly what to click.
- Everything about apps and data comes through ctzd: `ctzd open` for an existing app, `ctzd sources` and the app's
  department skill for data. Never search this computer's files for apps, databases or credentials: they are not on
  it, and the person's files are theirs.

## Logging in

If `ctzd setup` reports `login` as missing:

- `ctzd login --start --json` returns `user_code` and `verification_uri` and opens a browser tab on GitHub. Tell the
  person: "In the browser tab that just opened (or at VERIFICATION_URI), sign in with your work account if asked,
  enter the code USER_CODE and click Authorize."
- Then `ctzd login --wait --json`. It blocks until they approve, so give it a 15-minute timeout. It returns their
  `login` and `email`. If the code expired, start again.
- Run `ctzd setup --json` again until `ok` is true.

An `agent instructions` warning names a `CLAUDE.md` in a parent folder that would hide the app's instructions from
Claude Code. Explain this to the person and rename it only with their agreement.

## Start a new app

1. Ask what they want, in their words: what it shows or sends, for whom, and how often. Do not ask where the data
   lives, which database or table, or how to connect: they do not know, and the app's department skill and
   `ctzd sources` will say.
2. Their department, `--sbu`: `fi` (Fixed Income) or `mas` (Multi-Asset Solutions). If `ctzd new` says it does not
   know the department, its `next` lists the ones it does. An app can only use its own department's data.
3. The kind, `--kind`:
   - `dash`: a dashboard, with any number of scheduled jobs beside it (the usual choice);
   - `job`: only scheduled jobs, such as a weekly email or a table refreshed each morning.
4. A short name: 3 to 30 lowercase letters, digits or hyphens, starting with a letter, without the department
   (`bond-prices`, not `fi-bond-prices`). The dashboard will live at `https://SBU-NAME.ctzd.contoso.com`, and while
   it is being reviewed at `https://SBU-NAME.ctzd-dev.contoso.com`. Confirm the name with the person in one line.
5. Run `ctzd new NAME --sbu SBU --kind dash --json` (or `--kind job`). It creates the app's GitHub repository, writes
   the template for the department's kit, pushes it and installs it, in a folder named NAME under the current folder
   (`--dir PARENT` puts it elsewhere). It returns `path`, `url` and `guide`, the app's `AGENTS.md`: read `guide` now,
   since your editor may hide that file. Never add `--offline`: it is for platform tests, and an app made with it
   can never be deployed.
6. Errors: "already exists and is not empty" means choose another name or `--dir`; "does not know the owner's
   email" means log in first, or pass `--owner EMAIL`; a refusal to create an app on a department's kit means the
   person is not in that department's team on GitHub: tell them who to ask, in plain words.

## Open an existing app

If they give a link such as `https://fi-bond-prices.ctzd.contoso.com` (or `.ctzd-dev.`), the department is `fi` and
the name is `bond-prices`. Run `ctzd open NAME --sbu SBU --json` (`--dir PARENT` to choose where). It returns `path`,
`url` and `guide`, the app's `AGENTS.md`, to read now. If it says they have no access, the app's owner or a colleague
in the department must add them; tell the person.

## Then work inside the app

- Work from `path`: every app command finds the app from the current folder.
- Read `guide` (`path/AGENTS.md`), then the skills in `path/.agents/skills/` it names, starting with `ctzd-workflow`,
  `ctzd-data`, the department's `sbu-SBU`, and `ctzd-pages` for a dashboard or `ctzd-jobs` for a job, before you
  write code. If you were started in a parent folder, your agent did not load them: read them by path. Cursor hides
  gitignored files from its file tools unless the app folder is the open folder; if a read is refused, print the
  file in the terminal instead.
- For later sessions, suggest the person starts their agent in the app folder (in Cursor, **File > Open Folder**),
  so it loads these instructions by itself.
