#!/usr/bin/env bash
# End to end: build the kits and what they build on as local wheels, make one app of each template on each kit with
# `ctzd new --offline`, and run `ctzd check` in each. Each dashboard then gets the ctzd-pages skill's page with nothing
# to choose, written as an agent would, passes every check again, and is opened in a real browser
# (scripts/browser_check.py, with the reload proof). Needs pypi.org for everything else.
#   scripts/e2e.sh [WORK_DIR]
# E2E_KITS and E2E_KINDS narrow the apps; E2E_BROWSER is chromium (Playwright's, the default), msedge or chrome (an
# installed browser), or off. Screenshots and each browser report land in WORK_DIR/browser/APP.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=${1:-$(mktemp -d)}
mkdir -p "$work/wheels" "$work/apps" "$work/home"
cd "$repo"
for package in ctzd-kit ctzd-common ctzd-fi ctzd-mas fi-risklib; do
    uv build --quiet --package "$package" --out-dir "$work/wheels"
done
# The bundled platform config, with the internal index swapped for the local wheels plus PyPI.
cp packages/ctzd-cli/src/ctzd_cli/platform.toml "$work/platform.toml"
cat >> "$work/platform.toml" <<TOML

[build_overrides]
python_index = [{name = "local", url = "$work/wheels", format = "flat"}, {name = "pypi", url = "https://pypi.org/simple", default = true}]
TOML
export CTZD_PLATFORM_CONFIG="$work/platform.toml" CTZD_HOME="$work/home"
status=0
for sbu in ${E2E_KITS:-fi mas}; do
    for kind in ${E2E_KINDS:-dash job}; do
        app="e2e-$sbu-$kind"
        rm -rf "${work:?}/apps/$app"
        if ! created=$("$repo/.venv/bin/ctzd" new "$app" --sbu "$sbu" --kind "$kind" --offline --owner e2e@contoso.com --dir "$work/apps" 2>&1); then
            echo "e2e: ctzd new for $app FAILED:" >&2
            echo "$created" >&2
            status=1
            continue
        fi
        if ! (cd "$work/apps/$app" && "$repo/.venv/bin/ctzd" check); then
            echo "e2e: $app FAILED" >&2
            status=1
            continue
        fi
        # Guidance is gitignored, and a passing check leaves nothing else to commit: the template is in ruff's format.
        left=$(git -C "$work/apps/$app" status --porcelain)
        if [ -n "$left" ] || [ ! -f "$work/apps/$app/AGENTS.md" ] || [ ! -d "$work/apps/$app/.claude/skills/sbu-$sbu" ]; then
            echo "e2e: $app left changes or is missing its guidance: $left" >&2
            status=1
            continue
        fi
        if [ "$kind" = dash ] && [ "${E2E_BROWSER:-chromium}" != off ]; then
            skill="$repo/packages/ctzd-cli/src/ctzd_cli/assets/skills/common/ctzd-pages/SKILL.md"
            awk '/^## A page with nothing to choose/{s=1} s&&/^```python/{f=1;next} f&&/^```/{exit} f' "$skill" > "$work/apps/$app/src/app/pages/today.py"
            if ! (cd "$work/apps/$app" && "$repo/.venv/bin/ctzd" check); then
                echo "e2e: $app FAILED with the skill's page with nothing to choose" >&2
                status=1
                continue
            fi
            channel=()
            if [ "${E2E_BROWSER:-chromium}" != chromium ]; then channel=(--channel "$E2E_BROWSER"); fi
            if ! "$repo/.venv/bin/python" "$repo/scripts/browser_check.py" "$work/apps/$app" --out "$work/browser/$app" --ctzd "$repo/.venv/bin/ctzd" --reload "${channel[@]}"; then
                echo "e2e: $app FAILED in the browser; see $work/browser/$app" >&2
                status=1
                continue
            fi
        fi
        echo "e2e: $app passed every check"
    done
done
exit $status
