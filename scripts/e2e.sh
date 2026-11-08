#!/usr/bin/env bash
# End-to-end check of the app template: build the libraries, generate one app of each kind
# with `ctzd new --offline`, and run `ctzd check` in each. Needs network access to pypi.org.
#   scripts/e2e.sh [WORK_DIR]
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=${1:-$(mktemp -d)}
mkdir -p "$work/wheels" "$work/apps" "$work/home"
cd "$repo"
uv build --quiet --package ctzd-core --out-dir "$work/wheels"
uv build --quiet --package ctzd-fi --out-dir "$work/wheels"

# The bundled platform config with the internal index swapped for local wheels plus PyPI.
python3 - "$repo/packages/ctzd-cli/src/ctzd_cli/platform.toml" "$work" <<'PY'
import pathlib, re, sys
source, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
text = re.sub(
    r'\[\[python\.index\]\]\nname = "internal"\nurl = "[^"]+"\ndefault = true\n',
    f'[[python.index]]\nname = "local"\nurl = "{work}/wheels"\nformat = "flat"\n\n[[python.index]]\nname = "pypi"\nurl = "https://pypi.org/simple"\ndefault = true\n',
    source.read_text(),
)
(work / "platform.toml").write_text(text)
PY
export CTZD_PLATFORM_CONFIG="$work/platform.toml" CTZD_HOME="$work/home"
status=0
for ui in react none; do
    app="e2e-$ui"
    rm -rf "${work:?}/apps/$app"
    uv run ctzd new "$app" --sbu fi --ui "$ui" --offline --owner e2e@contoso.com --dir "$work/apps" >/dev/null
    if ! (cd "$work/apps/$app" && "$repo/.venv/bin/ctzd" check); then
        echo "e2e: $ui app FAILED" >&2
        status=1
        continue
    fi
    # Guidance and its stamp are gitignored, and a passing check leaves nothing else to commit.
    left=$(git -C "$work/apps/$app" status --porcelain)
    if [ -n "$left" ] || [ ! -f "$work/apps/$app/AGENTS.md" ] || [ ! -d "$work/apps/$app/.claude/skills/ctzd-workflow" ]; then
        echo "e2e: $ui app left changes or is missing its guidance: $left" >&2
        status=1
        continue
    fi
    echo "e2e: $ui app passed every check"
done
exit $status
