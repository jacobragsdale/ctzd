// Proposal for the infrastructure owner.
// Every app repo's Jenkinsfile is `@Library('ctzd') _` followed by `ctzdApp()`.
// Needs: Pipeline Utility Steps (readJSON), Lockable Resources (lock), Credentials Binding; two agent labels:
// - `ctzd-check` with uv, git, Node, and pnpm, holding no credentials and no cluster access: the app's own
//   tests, and every package it installs, run there;
// - `ctzd` with uv, git, docker (or buildkit), helm, kubectl, and Playwright's Chromium, which holds the cluster
//   access and runs nothing from the app outside a docker build.
// Credentials `ctzd-github` (a token, bound only in the stages that push) and `ctzd-acr`.
// The defaults below are production's; another environment (a throwaway namespace, a local copy of the
// platform) overrides them through the library's global properties or `ctzdApp(registry: ...)`.
// The parameter is not called `options`: that name would hide the pipeline's own `options { }` section.

def call(Map config = [:]) {
    // The one ctzd version every build uses, released together with the chart it renders values for; the
    // rollback job reads the same file.
    def pins = ctzdVersions()
    def defaults = [
        cliVersion: pins.cli,
        registry: 'contosoctzd.azurecr.io',
        baseImage: 'contosoctzd.azurecr.io/ctzd/base:py3.14',
        namespace: 'ctzd-apps',
        chart: 'oci://contosoctzd.azurecr.io/helm/ctzd-app',
        chartVersion: pins.chart,
        dagsRepo: 'https://github.contoso.com/ctzd-platform/ctzd-dags.git',
        appScheme: 'https',
    ]
    def settings = defaults.collectEntries { key, value -> [key, config.get(key, env."CTZD_${key.replaceAll(/([A-Z])/, '_$1').toUpperCase()}" ?: value)] }
    // The Organization Folder names each build ctzd-apps/<repo>/<branch>; the rollback job takes the same lock,
    // so a rollback never interleaves with a deploy of the same app.
    def lockName = "ctzd-app-${env.JOB_NAME.tokenize('/')[-2]}"
    pipeline {
        agent none
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
        }
        stages {
            stage('Checks') {
                // The app's tests and its packages run here, with no token and no cluster access. The checks
                // need no login: the platform API serves release contents to anyone.
                agent { label config.get('checkAgent', 'ctzd-check') }
                steps {
                    script { ctzdInstall() }
                    sh '"$CTZD" ci check --json'
                }
            }
            stage('Ship') {
                when { branch 'main' }
                agent { label config.get('agent', 'ctzd') }
                stages {
                    stage('Prepare') {
                        steps {
                            script {
                                ctzdInstall()
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
                            }
                        }
                    }
                    stage('Image') {
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
                    stage('Go live') {
                        // Under the app's lock the pause is read again, so a rollback that ran while this build
                        // was checking or building is never overwritten by it.
                        options { lock(resource: lockName) }
                        stages {
                            stage('State') {
                                steps {
                                    script {
                                        env.PAUSED = sh(
                                            script: 'kubectl -n $NAMESPACE get configmap ctzd-state-$FULLNAME -o jsonpath={.data.paused} 2>/dev/null || true',
                                            returnStdout: true,
                                        ).trim()
                                    }
                                }
                            }
                            stage('Deploy') {
                                when { expression { env.PAUSED != 'true' } }
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
                                when { expression { env.PAUSED != 'true' && env.HAS_DASHBOARD == 'true' } }
                                steps {
                                    script {
                                        def status = sh(script: '"$CTZD" ci smoke --url "$APP_SCHEME://$HOST" --json', returnStatus: true)
                                        if (status != 0) {
                                            sh 'helm rollback "$FULLNAME" --namespace "$NAMESPACE" --wait --timeout 5m'
                                            error('The dashboard showed an error after the rollout; the previous release is back.')
                                        }
                                    }
                                }
                            }
                            stage('Jobs') {
                                // After the smoke test, so Airflow only ever runs the image of a release that went live.
                                when { expression { env.PAUSED != 'true' } }
                                steps {
                                    withCredentials([string(credentialsId: 'ctzd-github', variable: 'CTZD_GITHUB_TOKEN')]) {
                                        sh 'SPEC="$WORKSPACE@tmp/out/airflow-spec.json" bash -euo pipefail "$WORKSPACE@tmp/publish-spec.sh"'
                                    }
                                }
                            }
                            stage('Release') {
                                when { expression { env.PAUSED != 'true' } }
                                steps {
                                    script {
                                        def release = withCredentials([string(credentialsId: 'ctzd-github', variable: 'CTZD_GITHUB_TOKEN')]) {
                                            readJSON(text: sh(script: '"$CTZD" ci release --image "$IMAGE" --commit "$GIT_COMMIT" --json', returnStdout: true))
                                        }
                                        env.RELEASE_NUMBER = "${release.release as int}"
                                        currentBuild.description = "release v${env.RELEASE_NUMBER}"
                                        sh '''
                                            kubectl -n "$NAMESPACE" create configmap "ctzd-state-$FULLNAME" \
                                                --from-literal=release="$RELEASE_NUMBER" --from-literal=paused=false \
                                                --dry-run=client -o yaml | kubectl apply -f -
                                        '''
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        post {
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
