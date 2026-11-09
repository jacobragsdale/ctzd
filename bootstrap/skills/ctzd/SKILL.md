---
name: ctzd
description: "Set up ctzd on this computer, start a new ctzd app (a dashboard, scheduled jobs, or both), or open an existing one to change or fix it. Use when the person mentions ctzd or a ctzd link (https://SBU-NAME.ctzd.contoso.com), names one of their dashboards, reports, or scheduled emails, or wants one built, changed, or fixed. Use it before looking for the app anywhere else."
allowed-tools: Bash(ctzd *) PowerShell(ctzd *)
---

# ctzd

ctzd builds and runs Python dashboards and scheduled jobs for people who do
not code. You do every technical step with the `ctzd` command; the person
never runs a command, opens a terminal, or uses git. Never search this
computer for apps, data, or credentials: they come through `ctzd`, and the
person's files are theirs.

1. If `ctzd` is not found, install it. On Windows run
   `powershell -c "irm https://ctzd.contoso.com/install.ps1 | iex"`. The
   script prints the full path of `ctzd.exe`. Your shell may not find `ctzd`
   by name until the agent restarts, so until then run it by that full path.
2. Run `ctzd setup --json` and do what its `next` says, until `ok` is true.
   Every ctzd command answers in JSON with `ok`, `summary`, and `next`.
3. Then read `guide` in that output. It is the platform's current guide to
   starting a new app or opening an existing one; follow it.
