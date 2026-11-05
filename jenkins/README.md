# Jenkins (proposal)

Untested proposals for the infrastructure owner; see section 11 of `docs/plan.md`.

- `vars/ctzdApp.groovy`: the shared-library step every app's `Jenkinsfile` calls. One GitHub
  Organization Folder job scans the `ctzd-apps` org; branches other than `main` run the checks only.
- `jobs/ctzd-rollback.Jenkinsfile`: redeploys a release's image and config and pauses auto-deploy.
- `jobs/ctzd-delete.Jenkinsfile`: removes an app's dashboard and job specs.
- `resources/ctzd/`: the chart's platform values and the script that publishes job specs to `ctzd-dags`.

The pipeline runs the `ctzd` CLI at the version recorded in each app's `.ctzd/version`, so
platform upgrades never change the checks an app is built with until the app is synced.
