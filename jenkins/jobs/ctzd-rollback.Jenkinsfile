// Proposal: the parameterised job the platform API triggers for `ctzd rollback`.
// Redeploys a release's image and config (no rebuild) and leaves auto-deploy paused.
// It runs in the Groovy sandbox like any job, so the git auth header is built in the shell, and it loads the
// ctzd library for libraryResource and the versions ctzdApp() uses.
// Nothing from the release tag is pasted into a shell command: anyone who can push to an app can write a tag,
// so its fields reach the shell as environment variables, after the commit is checked to be a commit id.
@Library('ctzd') _

def pins = ctzdVersions()

pipeline {
    agent { label 'ctzd' }
    parameters {
        string(name: 'REPO', description: 'app repo, such as fi-bond-prices')
        string(name: 'RELEASE', description: 'release number to restore')
    }
    options {
        timeout(time: 15, unit: 'MINUTES')
        // The same lock ctzdApp() takes around going live, so a build of the app never overwrites this rollback.
        lock(resource: "ctzd-app-${params.REPO}")
    }
    environment {
        ORG_URL = 'https://github.contoso.com/ctzd-apps'
        NAMESPACE = 'ctzd-apps'
        CHART = 'oci://contosoctzd.azurecr.io/helm/ctzd-app'
        CHART_VERSION = "${pins.chart}"
        CTZD_VERSION = "${pins.cli}"
        DAGS_REPO = 'https://github.contoso.com/ctzd-platform/ctzd-dags.git'
    }
    stages {
        stage('Restore') {
            steps {
                script {
                    if (!(params.REPO ==~ /[a-z0-9][a-z0-9-]*/) || !(params.RELEASE ==~ /[0-9]+/)) {
                        error("REPO must be an app repo name and RELEASE a number; got '${params.REPO}' and '${params.RELEASE}'.")
                    }
                    withCredentials([string(credentialsId: 'ctzd-github', variable: 'CTZD_GITHUB_TOKEN')]) {
                        sh '''
                            host=$(printf '%s' "$ORG_URL" | sed -E 's#https://([^/]+)/.*#\\1#')
                            auth="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$CTZD_GITHUB_TOKEN" | base64 -w0)"
                            rm -rf app && git -c "http.https://$host/.extraheader=$auth" clone --quiet "$ORG_URL/$REPO.git" app
                            git -C app -c "http.https://$host/.extraheader=$auth" fetch --quiet --tags
                        '''
                    }
                    def record = readJSON(text: sh(script: 'git -C app tag -l --format="%(contents)" "ctzd/v$RELEASE"', returnStdout: true))
                    if (!(record.commit instanceof String) || !(record.commit ==~ /[0-9a-f]{40}/)) {
                        error("The tag ctzd/v${params.RELEASE} does not name a commit.")
                    }
                    env.RESTORE_COMMIT = record.commit
                    env.IMAGE = "${record.image}"
                    sh 'git -C app checkout --quiet "$RESTORE_COMMIT"'
                    writeFile(file: 'app/app.toml', text: record.app_toml)
                    dir('app') {
                        ctzdInstall()
                        sh '"$CTZD" ci render --image "$IMAGE" --release "$RELEASE" --out out --json'
                        def values = readJSON(file: 'out/helm-values.json')
                        env.FULLNAME = values.fullname
                        env.SBU = values.sbu
                        env.APP = values.app
                        if (values.enabled) {
                            writeFile(file: 'platform-values.yaml', text: libraryResource('ctzd/platform-values.yaml'))
                            sh 'helm upgrade --install "$FULLNAME" "$CHART" --version "$CHART_VERSION" --namespace "$NAMESPACE" --atomic --timeout 5m -f platform-values.yaml -f out/helm-values.json'
                        }
                        writeFile(file: 'publish-spec.sh', text: libraryResource('ctzd/publish-spec.sh'))
                        withCredentials([string(credentialsId: 'ctzd-github', variable: 'CTZD_GITHUB_TOKEN')]) {
                            sh 'SPEC=out/airflow-spec.json bash -euo pipefail publish-spec.sh'
                        }
                    }
                    sh '''
                        kubectl -n "$NAMESPACE" create configmap "ctzd-state-$FULLNAME" \
                            --from-literal=release="$RELEASE" --from-literal=paused=true \
                            --dry-run=client -o yaml | kubectl apply -f -
                    '''
                    currentBuild.description = "${params.REPO} rolled back to v${params.RELEASE}"
                }
            }
        }
    }
}
