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

# The bundled platform config, with release overrides that swap the release's internal index for local wheels plus
# PyPI and, when CTZD_BASIS_UI_URL is set (a URL, or `file:` and a folder), take basis-ui's packages from there
# (basis-ui.tgz and basis-parallax.tgz), as CI packs them from basis-ui's main branch.
# It prints "react" when a React app can be built here: pnpm is installed and both packages are reachable.
react=$(python3 - "$repo/packages/ctzd-cli/src/ctzd_cli" "$work" "${CTZD_BASIS_UI_URL:-}" <<'PY'
import pathlib, shutil, sys, tomllib
cli, work, url = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3].rstrip("/")
text = (cli / "platform.toml").read_text()
text += f"""
[release_overrides]
python_index = [{{name = "local", url = "{work}/wheels", format = "flat"}}, {{name = "pypi", url = "https://pypi.org/simple", default = true}}]
"""
specs = tomllib.loads((cli / "assets" / "release.toml").read_text())["npm"]["packages"]
if url:
    specs = {"@basis/ui": f"{url}/basis-ui.tgz", "@basis/parallax": f"{url}/basis-parallax.tgz"}
    text += f'npm_packages = {{"@basis/ui" = "{specs["@basis/ui"]}", "@basis/parallax" = "{specs["@basis/parallax"]}"}}\n'
(work / "platform.toml").write_text(text)
local = [pathlib.Path(spec.removeprefix("file:")).expanduser() for spec in specs.values() if spec.startswith("file:")]
print("react" if shutil.which("pnpm") and specs and all(path.is_file() for path in local) else "")
PY
)
export CTZD_PLATFORM_CONFIG="$work/platform.toml" CTZD_HOME="$work/home"
kinds="none"
if [ "$react" = "react" ]; then
    kinds="react none"
else
    echo "e2e: SKIPPED the React app: it needs pnpm and basis-ui's packages (set CTZD_BASIS_UI_URL)" >&2
fi
status=0
for ui in $kinds; do
    app="e2e-$ui"
    rm -rf "${work:?}/apps/$app"
    if ! created=$(uv run ctzd new "$app" --sbu fi --ui "$ui" --offline --owner e2e@contoso.com --dir "$work/apps" 2>&1); then
        echo "e2e: ctzd new for the $ui app FAILED:" >&2
        echo "$created" >&2
        status=1
        continue
    fi
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
