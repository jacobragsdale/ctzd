---
name: ctzd-workflow
description: "Take a ctzd app from a change to prod: ctzd check, ctzd deploy (a pull request and the dev address), sending the review link to a colleague, the approval and auto-merge, ctzd promote and its second approval, ctzd status, releases, rollback, logs and delete, and logging in to GitHub. Use for any request to try, ship ('make it live', 'send it out'), check on, undo or remove the app, and whenever a command says to log in."
---

# From a change to prod

## Rules

- You run every command, from the app folder (the one with `app.toml`), always with `--json`. Read `ok`, `summary`
  and `next` first: `next` says what to do. Exit status 0 is success, 1 a failure, 2 a wrong command line.
- The person never uses GitHub beyond clicking a link you give them. Never ask them to open a pull request, merge,
  press a button on GitHub, or read a page there. Never run git to commit, push, branch or merge yourself.
- A change is ready when `ctzd check --json` passes. It is on dev when `ctzd deploy --json` says so, and live for the
  business only after `ctzd promote --json` has gone through.

## The path

```text
change ─▶ ctzd check ─▶ ctzd deploy ─▶ dev address + review link ─▶ colleague approves ─▶ merged: release N
                                                                                                │
                      prod live, owners emailed ◀── colleague approves ◀── ctzd promote ◀───────┘
```

1. **`ctzd check --json`** runs every gate (`ctzd-troubleshoot` lists them). It fixes formatting and simple lint
   itself (`"state": "fixed"`). `--fast` skips the tests and job runs while you iterate. Run it in full before you
   say you are done.
2. **`ctzd deploy --json`** runs the checks again, puts the change on the app's branch and opens a pull request, or
   adds to the one already open, with a description in plain words. Then it follows CI: the same checks, the image
   built, and the change put on the app's **dev address**. It returns the dev address, each job's Airflow link in
   dev, and the pull request's link. It refuses an app whose template sample page or sample job is still in place.
   An app has one open pull request, because it has one dev address: deploy again to add to it.
3. **Send the two links.** Tell the person, in these words or close to them:
   "Your change is ready to look at here: DEV_ADDRESS. Send this link to a colleague to look over: REVIEW_LINK.
   Once they approve it, it is saved as the new version." Any colleague can approve; it cannot be the person
   themself. New changes after an approval need a fresh one.
4. **The approval merges it by itself.** `ctzd deploy` turned on auto-merge, so nobody presses anything: once the
   checks are green and a colleague has approved, the change merges and becomes a release, `ctzd/vN`. Dev already
   runs it.
5. **`ctzd promote --json`** asks for the newest release to go to prod (`--release N` for another). It prints an
   approval link and follows it. Tell the person: "Send this link to a colleague to approve the release: LINK."
   It may be the same colleague; it cannot be the person who promotes. Once approved, prod runs exactly what dev
   ran, the jobs' schedules start, and the app's owners get an email with the dashboard's address and each job's
   Airflow link.
6. **`ctzd status --json`** shows the release live in each environment, what is waiting for whose approval, the jobs
   with their links, and their last runs. Use it whenever the person asks "is it live?" or "did it run?".

The addresses are `https://SBU-NAME.ctzd-dev.contoso.com` (dev) and `https://SBU-NAME.ctzd.contoso.com` (prod).

## The live app

| The person says | Do |
|---|---|
| "the live dashboard is wrong" | `ctzd rollback --json` first: it puts the release that was live before back on prod at once, with no approval. Tell them it is back to the previous version. Then fix, `ctzd deploy`, and promote again. |
| "go back to how it was last week" | `ctzd releases --json` lists every release with its date; `ctzd rollback --release N --json` puts that one back. Only a release that was live on prod before can come back. |
| "is it working?" | `ctzd status --json`. For the dashboard's own log, `ctzd logs --json` (prod) or `ctzd logs --env dev --json`. |
| "run the job now" | `ctzd run JOB --env prod --json`, or `--env dev` to try it in dev first (`ctzd-jobs`). |

Dev has no rollback: the next `ctzd deploy` replaces what it runs.

## Removing the app

`ctzd delete --json` removes the dashboard and the jobs from dev and prod and archives the app; its tables go after
30 days. It waits for a colleague's approval, like a promote, and prints the link to send. Run it only when the
person clearly asks for the app to be removed, after confirming with them in one plain sentence.

## Logging in

When a command says the person is not logged in to GitHub, or that GitHub refused the login:

1. `ctzd login --start --json` returns `user_code` and `verification_uri` and opens a browser tab. Tell the person:
   "In the browser tab that just opened (or at VERIFICATION_URI), sign in if asked, enter the code USER_CODE and
   click Authorize."
2. `ctzd login --wait --json` blocks until they approve, so give it a 15-minute timeout. It returns `login` and
   `email`. If the code expired, start again.

`ctzd env` is the other sign-in: the person's work account, in the browser, once for real data on this desktop.

## Keeping current

Every command first brings the app's locked files and guidance to this ctzd's version (`ctzd sync` does only that).
When a newer ctzd is out, `ctzd deploy` stops and says to run `uv tool upgrade ctzd`: run it, then the same command
again. Neither needs explaining to the person.

## Telling the person

| Result | Say |
|---|---|
| on dev | "Your change is ready to look at: DEV_ADDRESS. Send this link to a colleague to look over: REVIEW_LINK." |
| approved and merged | "Your colleague approved it. When you want it live for everyone, I will send it out." |
| promote waiting | "Send this link to a colleague to approve the release: LINK. It goes live as soon as they do." |
| live on prod | "It is live: PROD_ADDRESS. You will also get an email with the link." |
| deploy failed | "The change did not go out because REASON. Nothing anyone sees has changed. I am fixing it now." |
| rolled back | "I put the previous version back; it is live now. Next I will fix the change and send it again." |
