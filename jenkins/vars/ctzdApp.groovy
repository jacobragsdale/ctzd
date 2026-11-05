// Proposal for the infrastructure owner: not yet run on a real Jenkins.
// Every app repo's Jenkinsfile is `@Library('ctzd') _` followed by `ctzdApp()`.
// Needs: Pipeline Utility Steps (readJSON), an agent labelled `ctzd` with uv, git, docker (or buildkit),
// helm, kubectl, and Playwright's Chromium; credentials `ctzd-github` (token) and `ctzd-acr`.

def call(Map options = [:]) {
    pipeline {
        agent { label options.get('agent', 'ctzd') }
        options {
            disableConcurrentBuilds()
            timeout(time: 30, unit: 'MINUTES')
            buildDiscarder(logRotator(numToKeepStr: '30'))
        }
        environment {
            REGISTRY = 'contosoctzd.azurecr.io'
            BASE_IMAGE = 'contosoctzd.azurecr.io/ctzd/base:py3.13'
            NAMESPACE = 'ctzd-apps'
            CHART = 'oci://contosoctzd.azurecr.io/helm/ctzd-app'
            CHART_VERSION = '0.1.0'
            DAGS_REPO = 'https://github.contoso.com/ctzd-platform/ctzd-dags.git'
            CTZD_GITHUB_TOKEN = credentials('ctzd-github')
        }
        stages {
            stage('Prepare') {
                steps {
                    script {
                        // The CLI at the version the app's platform files came from, in a venv outside the workspace.
                        env.CTZD_VERSION = readFile('.ctzd/version').trim()
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
                    sh 'SPEC="$WORKSPACE@tmp/out/airflow-spec.json" bash -euo pipefail "$WORKSPACE@tmp/publish-spec.sh"'
                }
            }
            stage('Smoke test') {
                when { allOf { branch 'main'; expression { env.PAUSED != 'true' && env.HAS_DASHBOARD == 'true' } } }
                steps {
                    script {
                        def status = sh(script: '"$CTZD" ci smoke --url "https://$HOST" --screenshot screenshot.png --json', returnStatus: true)
                        if (status != 0) {
                            sh 'helm rollback "$FULLNAME" --namespace "$NAMESPACE" --wait --timeout 5m'
                            error('The dashboard showed an error after the rollout; the previous release is back.')
                        }
                    }
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
