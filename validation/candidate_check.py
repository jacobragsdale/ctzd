"""Before a platform release is promoted: every app in the apps org, checked on it.

    CTZD_GITHUB_TOKEN=... uv run python -m validation.candidate_check RELEASE [WORK_DIR]

For each repository in the org, this clones it, runs `ctzd ci check --candidate RELEASE --json` (the app moved to
the release, with every automatic fix, as an upgrade on a desktop would do it), and reports which apps pass, which
files the automatic fixes change, and the checks that still fail. It exits 1 when any app fails. Promote the release
(move a channel to it in channels.toml) only when every app passes, or after the failing apps are fixed, or mark the
release breaking with a deadline (see assets/release.toml).

The token needs read access to the org's repositories and nothing else. The checks run each app's tests and
packages, so run this where no platform credentials are, as Jenkins runs `ctzd ci check`.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from ctzd_cli import config
from validation.harness import CTZD, tool

_PAGE = 100
_TIMEOUT = 60.0


def repositories(api_url: str, org: str, token: str) -> list[str]:
    names: list[str] = []
    url: str | None = f"{api_url.rstrip('/')}/orgs/{org}/repos"
    params: dict[str, int | str] | None = {"per_page": _PAGE, "type": "all"}
    with httpx.Client(headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, timeout=_TIMEOUT) as client:
        while url is not None:
            response = client.get(url, params=params)
            response.raise_for_status()
            names += [str(repo["name"]) for repo in response.json() if not repo.get("archived")]
            url, params = response.links.get("next", {}).get("url"), None
    return sorted(names)


def check(repo_url: str, folder: Path, release: int, token: str) -> dict[str, object]:
    header = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    host = repo_url.split("/")[2]
    cloned = subprocess.run([tool("git"), "-c", f"http.https://{host}/.extraheader={header}", "clone", "--quiet", "--depth", "1", repo_url, str(folder)], capture_output=True, text=True, check=False)
    if cloned.returncode != 0:
        return {"ok": False, "summary": f"could not clone: {cloned.stderr.strip()}"}
    env = {key: value for key, value in os.environ.items() if key != "CTZD_GITHUB_TOKEN"}  # the app's tests never see the token
    done = subprocess.run([str(CTZD), "ci", "check", "--candidate", str(release), "--json"], cwd=folder, capture_output=True, text=True, check=False, env=env)
    try:
        parsed: object = json.loads(done.stdout)
    except ValueError:
        return {"ok": False, "summary": (done.stdout + done.stderr).strip()[-2000:]}
    body = parsed if isinstance(parsed, dict) else {}
    steps = body.get("steps")
    failed = [step for step in steps if isinstance(step, dict) and step.get("state") == "failed"] if isinstance(steps, list) else []
    return {"ok": body.get("ok") is True, "summary": body.get("summary"), "changed": body.get("changed", []), "failed": failed}


def main(argv: list[str]) -> int:
    if not argv or not argv[0].isdigit():
        sys.stderr.write(__doc__ or "")
        return 2
    release = int(argv[0])
    token = os.environ.get("CTZD_GITHUB_TOKEN", "")
    if not token:
        sys.stderr.write("Set CTZD_GITHUB_TOKEN to a token that can read the apps org.\n")
        return 2
    platform = config.load()
    work = Path(argv[1]) if len(argv) > 1 else Path(tempfile.mkdtemp(prefix="ctzd-candidate-"))
    results: dict[str, dict[str, object]] = {}
    for name in repositories(platform.github.api_url, platform.github.org, token):
        results[name] = check(f"https://{platform.github.host}/{platform.github.org}/{name}.git", work / name, release, token)
        sys.stderr.write(f"{name}: {'passes' if results[name]['ok'] else 'FAILS'}\n")
    failing = sorted(name for name, result in results.items() if not result["ok"])
    sys.stdout.write(json.dumps({"release": release, "apps": len(results), "failing": failing, "results": results}, indent=2) + "\n")
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
