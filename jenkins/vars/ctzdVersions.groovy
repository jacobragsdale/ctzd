// The one CLI and chart version every ctzd job uses: ctzdApp() for builds and the rollback job.
// They live in resources/ctzd/versions.json, which the platform's tests compare with the CLI's and the chart's
// own versions, so the two jobs can never drift apart again.
def call() {
    return readJSON(text: libraryResource('ctzd/versions.json'))
}
