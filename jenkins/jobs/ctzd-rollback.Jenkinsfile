// Proposal: the parameterised job the platform API triggers for `ctzd rollback`.
// Redeploys a release's image and config (no rebuild) and leaves auto-deploy paused.
pipeline {
    agent { label 'ctzd' }
    parameters {
        string(name: 'REPO', description: 'app repo, such as fi-bond-prices')
        string(name: 'RELEASE', description: 'release number to restore')
    }
    options { timeout(time: 15, unit: 'MINUTES') }
    environment {
        ORG_URL = 'https://github.contoso.com/ctzd-apps'
        NAMESPACE = 'ctzd-apps'
        CHART = 'oci://contosoctzd.azurecr.io/helm/ctzd-app'
        CHART_VERSION = '0.1.0'
        DAGS_REPO = 'https://github.contoso.com/ctzd-platform/ctzd-dags.git'
        CTZD_GITHUB_TOKEN = credentials('ctzd-github')
    }
    stages {
        stage('Restore') {
            steps {
                script {
                    def host = env.ORG_URL.replaceFirst('https://', '').tokenize('/')[0]
                    def auth = "AUTHORIZATION: basic " + "x-access-token:${env.CTZD_GITHUB_TOKEN}".bytes.encodeBase64().toString()
                    sh "rm -rf app && git -c 'http.https://${host}/.extraheader=${auth}' clone --quiet '${env.ORG_URL}/${params.REPO}.git' app"
                    sh "git -C app -c 'http.https://${host}/.extraheader=${auth}' fetch --quiet --tags"
                    def record = readJSON(text: sh(script: "git -C app tag -l --format='%(contents)' 'ctzd/v${params.RELEASE}'", returnStdout: true))
                    sh "git -C app checkout --quiet '${record.commit}'"
                    writeFile(file: 'app/app.toml', text: record.app_toml)
                    env.IMAGE = record.image
                    dir('app') {
                        env.CTZD_VERSION = readFile('.ctzd/version').trim()
                        env.CTZD_VENV = "${env.WORKSPACE}@tmp/ctzd-${env.CTZD_VERSION}"
                        env.CTZD = "${env.CTZD_VENV}/bin/ctzd"
                        sh 'test -x "$CTZD" || { uv venv --quiet "$CTZD_VENV" && uv pip install --quiet --python "$CTZD_VENV" "ctzd[ci]==$CTZD_VERSION"; }'
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
                        sh 'SPEC=out/airflow-spec.json bash -euo pipefail publish-spec.sh'
                    }
                    sh """
                        kubectl -n "\$NAMESPACE" create configmap "ctzd-state-\$FULLNAME" \
                            --from-literal=release=${params.RELEASE} --from-literal=paused=true \
                            --dry-run=client -o yaml | kubectl apply -f -
                    """
                    currentBuild.description = "${params.REPO} rolled back to v${params.RELEASE}"
                }
            }
        }
    }
}
