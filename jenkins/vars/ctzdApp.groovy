// Proposal for the infrastructure owner.
// Every app repo's Jenkinsfile is `@Library('ctzd') _` followed by `ctzdApp()`.
// Needs: Pipeline Utility Steps (readJSON), an agent labelled `ctzd` with uv, git, docker (or buildkit),
// helm, kubectl, and Playwright's Chromium; credentials `ctzd-github` (token) and `ctzd-acr`.
// The defaults below are production's; another environment (a throwaway namespace, a local copy of the
// platform) overrides them through the library's global properties or `ctzdApp(registry: ...)`.
// The parameter is not called `options`: that name would hide the pipeline's own `options { }` section.

def call(Map config = [:]) {
    // The one ctzd version every build uses, released together with the chart it renders values for.
    def defaults = [
        cliVersion: '0.2.0',
        registry: 'contosoctzd.azurecr.io',
        baseImage: 'contosoctzd.azurecr.io/ctzd/base:py3.13',
        namespace: 'ctzd-apps',
        chart: 'oci://contosoctzd.azurecr.io/helm/ctzd-app',
        chartVersion: '0.2.0',
        dagsRepo: 'https://github.contoso.com/ctzd-platform/ctzd-dags.git',
        appScheme: 'https',
    ]
    def settings = defaults.collectEntries { key, value -> [key, config.get(key, env."CTZD_${key.replaceAll(/([A-Z])/, '_$1').toUpperCase()}" ?: value)] }
    pipeline {
        agent { label config.get('agent', 'ctzd') }
        options {
            disableConcurrentBuilds()
            timeout(time: 30, unit: 'MINUTES')
            buildDiscarder(logRotator(numToKeepStr: '30'))
        }
        environment {
            CTZD_VERSION = "${settings.cliVersion}"
            REGISTRY = "${settings.registry}"
            BASE_IMAGE = "${settings.baseImage}"
            NAMESPACE = "${settings.namespace}"
            CHART = "${settings.chart}"
            CHART_VERSION = "${settings.chartVersion}"
            DAGS_REPO = "${settings.dagsRepo}"
            APP_SCHEME = "${settings.appScheme}"
            CTZD_GITHUB_TOKEN = credentials('ctzd-github')
        }
        stages {
            stage('Prepare') {
                steps {
                    script {
                        // The platform's CLI, in a venv outside the workspace; it checks the build files against
                        // the platform release the app records in .ctzd/version.
                        env.CTZD_VENV = "${env.WORKSPACE}@tmp/ctzd-${env.CTZD_VERSION}"
                        env.CTZD = "${env.CTZD_VENV}/bin/ctzd"
                        sh 'test -x "$CTZD" || { uv venv --quiet "$CTZD_VENV" && uv pip install --quiet --python "$CTZD_VENV" "ctzd[ci]==$CTZD_VERSION"; }'
                        writeFile(file: "${env.WORKSPACE}@tmp/platform-values.yaml", text: libraryResource('ctzd/platform-values.yaml'))
                        writeFile(file: "${env.WORKSPACE}@tmp/publish-spec.sh", text: libraryResource('ctzd/publish-spec.sh'))
                        sh '"$CTZD" ci render --image placeholder --out "$WORKSPACE@tmp/out" --json'
                        def values = readJSON(file: "${env.WORKSPACE}@tmp/out/helm-values.json")
                        env.FULLNAME = values.fullname
                        env.HOST = values.host
                        env.HAS_DASHBOARD = values.enabled ? 'true' : 'false'
                        env.SBU = values.sbu
                        env.APP = values.app
                        env.IMAGE = "${env.REGISTRY}/apps/${env.FULLNAME}:${env.GIT_COMMIT}"
                        env.PAUSED = sh(
                            script: 'kubectl -n $NAMESPACE get configmap ctzd-state-$FULLNAME -o jsonpath={.data.paused} 2>/dev/null || true',
                            returnStdout: true,
                        ).trim()
                    }
                }
            }
            stage('Checks') {
                steps { sh '"$CTZD" ci check --json' }
            }
            stage('Image') {
                when { branch 'main' }
                steps {
                    withCredentials([usernamePassword(credentialsId: 'ctzd-acr', usernameVariable: 'ACR_USER', passwordVariable: 'ACR_PASSWORD')]) {
                        sh '''
                            echo "$ACR_PASSWORD" | docker login "$REGISTRY" -u "$ACR_USER" --password-stdin
                            docker build --build-arg BASE_IMAGE="$BASE_IMAGE" -t "$IMAGE" .
                            docker push "$IMAGE"
                        '''
                    }
                }
            }
            stage('Deploy') {
                when { allOf { branch 'main'; expression { env.PAUSED != 'true' } } }
                steps {
                    sh '"$CTZD" ci render --image "$IMAGE" --out "$WORKSPACE@tmp/out" --json'
                    script {
                        if (env.HAS_DASHBOARD == 'true') {
                            sh '''
                                helm upgrade --install "$FULLNAME" "$CHART" --version "$CHART_VERSION" \
                                    --namespace "$NAMESPACE" --atomic --timeout 5m \
                                    -f "$WORKSPACE@tmp/platform-values.yaml" -f "$WORKSPACE@tmp/out/helm-values.json"
                            '''
                        }
                    }
                }
            }
            stage('Smoke test') {
                when { allOf { branch 'main'; expression { env.PAUSED != 'true' && env.HAS_DASHBOARD == 'true' } } }
                steps {
                    script {
                        def status = sh(script: '"$CTZD" ci smoke --url "$APP_SCHEME://$HOST" --screenshot screenshot.png --json', returnStatus: true)
                        if (status != 0) {
                            sh 'helm rollback "$FULLNAME" --namespace "$NAMESPACE" --wait --timeout 5m'
                            error('The dashboard showed an error after the rollout; the previous release is back.')
                        }
                    }
                }
            }
            stage('Jobs') {
                // After the smoke test, so Airflow only ever runs the image of a release that went live.
                when { allOf { branch 'main'; expression { env.PAUSED != 'true' } } }
                steps {
                    sh 'SPEC="$WORKSPACE@tmp/out/airflow-spec.json" bash -euo pipefail "$WORKSPACE@tmp/publish-spec.sh"'
                }
            }
            stage('Release') {
                when { allOf { branch 'main'; expression { env.PAUSED != 'true' } } }
                steps {
                    script {
                        def release = readJSON(text: sh(script: '"$CTZD" ci release --image "$IMAGE" --commit "$GIT_COMMIT" --json', returnStdout: true))
                        currentBuild.description = "release v${release.release}"
                        sh """
                            kubectl -n "\$NAMESPACE" create configmap "ctzd-state-\$FULLNAME" \
                                --from-literal=release=${release.release} --from-literal=paused=false \
                                --dry-run=client -o yaml | kubectl apply -f -
                        """
                    }
                }
            }
        }
        post {
            always { archiveArtifacts artifacts: 'screenshot.png', allowEmptyArchive: true }
            success {
                script {
                    if (env.BRANCH_NAME == 'main' && env.PAUSED == 'true') {
                        currentBuild.description = 'checks passed; auto-deploy is paused after a rollback'
                    }
                }
            }
        }
    }
}
