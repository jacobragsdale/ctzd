# Jenkins (proposal)

Untested proposals for the infrastructure owner; see section 11 of `docs/plan.md`.

- `vars/ctzdApp.groovy`: the shared-library step every app's `Jenkinsfile` calls. One GitHub
  Organization Folder job scans the `ctzd-apps` org; branches other than `main` run the checks only.
- `jobs/ctzd-rollback.Jenkinsfile`: redeploys a release's image and config and pauses auto-deploy.
- `jobs/ctzd-delete.Jenkinsfile`: removes an app's dashboard and job specs.
- `vars/ctzdVersions.groovy` and `resources/ctzd/versions.json`: the one CLI and chart version
  every job uses. A test in `packages/ctzd-cli/tests/test_platform_pins.py` checks they match the
  CLI and the chart in this repository, and that no job pins its own.
- `resources/ctzd/`: the chart's platform values and the script that publishes job specs to `ctzd-dags`.

Every build runs the platform's one pinned CLI. That CLI checks the build files against the
platform release recorded in each app's `.ctzd/version`, so a platform upgrade never changes the
checks an app is built with until the app is synced.

Two agent labels keep app code away from the platform's keys. The checks, which run the app's
tests and install its packages, run on `ctzd-check`, which holds no credentials and no cluster
access; they need no login, because the platform API serves release contents to anyone. Everything
after the checks runs on `ctzd`, which holds the cluster access and runs nothing of the app's
outside a docker build. The GitHub token is bound only in the stages that push: the job specs and
the release tag.

Going live (deploy, smoke test, job specs, release tag) and the rollback job take the same lock,
`ctzd-app-<repo>`, and the pause is read under that lock, so a rollback that runs while a build is
checking or building is never overwritten when the build finishes.
