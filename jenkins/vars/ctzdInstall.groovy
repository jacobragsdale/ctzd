// Installs the platform's CLI ($CTZD_VERSION) into a venv outside the workspace and sets $CTZD to it.
def call() {
    env.CTZD_VENV = "${env.WORKSPACE}@tmp/ctzd-${env.CTZD_VERSION}"
    env.CTZD = "${env.CTZD_VENV}/bin/ctzd"
    sh 'test -x "$CTZD" || { uv venv --quiet "$CTZD_VENV" && uv pip install --quiet --python "$CTZD_VENV" "ctzd[ci]==$CTZD_VERSION"; }'
}
