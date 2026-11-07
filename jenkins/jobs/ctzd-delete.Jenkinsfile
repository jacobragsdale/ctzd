// Proposal: the job the platform API triggers for `ctzd delete`. The API archives the repo itself.
// It removes the app's job spec from ctzd-dags and records the deletion in deleted/<sbu>/<app>.json there;
// the ctzd_table_janitor DAG removes the app's tables from the store 30 days after that.
pipeline {
    agent { label 'ctzd' }
    parameters { string(name: 'REPO', description: 'app repo, such as fi-bond-prices') }
    options { timeout(time: 10, unit: 'MINUTES') }
    environment {
        NAMESPACE = 'ctzd-apps'
        DAGS_REPO = 'https://github.contoso.com/ctzd-platform/ctzd-dags.git'
        CTZD_GITHUB_TOKEN = credentials('ctzd-github')
    }
    stages {
        stage('Remove') {
            steps {
                sh 'helm uninstall "$REPO" --namespace "$NAMESPACE" --wait || true'
                sh 'kubectl -n "$NAMESPACE" delete configmap "ctzd-state-$REPO" --ignore-not-found'
                sh '''
                    host=$(printf '%s' "$DAGS_REPO" | sed -E 's#https://([^/]+)/.*#\\1#')
                    auth="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$CTZD_GITHUB_TOKEN" | base64 -w0)"
                    rm -rf dags-repo && git -c "http.https://$host/.extraheader=$auth" clone --quiet --depth 1 "$DAGS_REPO" dags-repo
                    sbu=${REPO%%-*}; app=${REPO#*-}
                    if [ -f "dags-repo/specs/$sbu/$app.json" ]; then
                        git -C dags-repo rm --quiet "specs/$sbu/$app.json"
                    fi
                    mkdir -p "dags-repo/deleted/$sbu"
                    printf '{"sbu": "%s", "app": "%s", "deleted_at": "%s"}\n' "$sbu" "$app" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "dags-repo/deleted/$sbu/$app.json"
                    git -C dags-repo add "deleted/$sbu/$app.json"
                    git -C dags-repo -c user.name=ctzd -c user.email=ctzd@localhost commit --quiet -m "Remove $REPO"
                    git -C dags-repo -c "http.https://$host/.extraheader=$auth" push --quiet origin HEAD:main
                '''
            }
        }
    }
}
