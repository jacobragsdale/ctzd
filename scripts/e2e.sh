#!/usr/bin/env bash
# End to end: build the kits and what they build on as local wheels, make one app of each template on each kit with
# `ctzd new --offline`, and run `ctzd check` in each. Needs pypi.org for everything else.
#   scripts/e2e.sh [WORK_DIR]
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
        echo "e2e: $app passed every check"
    done
done
exit $status
