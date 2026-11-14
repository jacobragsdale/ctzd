# ctzd-common

What every ctzd app may use beside its department's kit: `ctzd_common.email` (the mail relay, with frames attached as
xlsx or csv), `ctzd_common.slack` (the platform's bot, posting to a channel by name), `ctzd_common.display` (the one
display-rounding rule), and `ctzd_common.samples`. The `dash` extra pins the dashboard's stack. Messages follow the
environment: kept in `.ctzd/outbox/` locally, sent only to the app's owners in dev, sent as written in prod.
