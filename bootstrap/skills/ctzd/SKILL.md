---
name: ctzd
description: "Set up ctzd on this computer, start a new ctzd app (a Streamlit or Dash dashboard, or scheduled jobs), or open an existing one. Use when the person mentions ctzd or wants a dashboard, report, or scheduled email built and run for them."
---

# ctzd

ctzd builds and runs Python dashboards and scheduled jobs for people who do
not code. You do every technical step with the `ctzd` command; the person
never runs a command, opens a terminal, or uses git.

1. If `ctzd` is not found, install it. On Windows run
   `powershell -c "irm https://ctzd.contoso.com/install.ps1 | iex"`; if `ctzd`
   is still not found afterwards, use the path the script printed.
2. Run `ctzd setup --json` and do what its `next` says, until `ok` is true.
   Every ctzd command answers in JSON with `ok`, `summary`, and `next`.
3. Then read `guide` in that output. It is the platform's current guide to
   starting a new app or opening an existing one; follow it.
