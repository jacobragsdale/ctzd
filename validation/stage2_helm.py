"""Stage 2b (offline part): lint and render the Helm chart with real `ctzd ci render` output.

    uv run python -m validation.stage2_helm HELM [WORK_DIR]

Checks that each pod mounts only its app's source secrets, that the sign-in
annotations appear only when `auth.enabled` is set, and that the chart refuses
`auth.enabled` without its URLs. Installing into a cluster stays unrun here.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ctzd_cli import config, render
from ctzd_cli.appconfig import AppConfig
from validation.harness import REPO, Report

CHART = REPO / "deploy" / "helm" / "ctzd-app"
PLATFORM_VALUES = REPO / "jenkins" / "resources" / "ctzd" / "platform-values.yaml"
SOURCES = ("fi_pricing", "fi_trading")


def app(ui: str, sources: tuple[str, ...]) -> AppConfig:
    return AppConfig.model_validate({"app": {"name": "bond-prices", "sbu": "fi", "ui": ui, "owners": ["ann.lee@contoso.com"]}, "sources": {"use": list(sources)}})


def helm(binary: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([binary, *args], capture_output=True, text=True, check=False)


def main() -> int:
    if len(sys.argv) < 2:  # noqa: PLR2004 - the helm binary is required
        sys.stderr.write(__doc__ or "")
        return 2
    binary = sys.argv[1]
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="ctzd-helm-"))  # noqa: PLR2004 - the optional work folder
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    report = Report("stage 2b (Helm chart, offline)")
    values = work / "values.json"
    values.write_text(render.helm_values(config.load(), app("streamlit", SOURCES), image="reg/apps/fi-bond-prices:abc", release=3).model_dump_json(by_alias=True))
    linted = helm(binary, "lint", str(CHART), "-f", str(PLATFORM_VALUES), "-f", str(values))
    report.expect("the chart lints with the platform values and rendered values", ok=linted.returncode == 0, seen=linted.stdout + linted.stderr)
    rendered = helm(binary, "template", "fi-bond-prices", str(CHART), "-f", str(PLATFORM_VALUES), "-f", str(values))
    manifest = rendered.stdout
    report.expect("it renders", ok=rendered.returncode == 0, seen=rendered.stderr)
    report.expect(
        "the pod mounts exactly the app's two source secrets",
        ok=manifest.count("name: ctzd-source-") == len(SOURCES) and "ctzd-source-fi-pricing" in manifest and "ctzd-source-fi-trading" in manifest,
        seen=manifest,
    )
    report.expect("no shared all-sources secret is mounted", ok="ctzd-sources" not in manifest)
    report.expect("sign-in is off by default", ok="auth-url" not in manifest)
    signed = helm(binary, "template", "fi-bond-prices", str(CHART), "-f", str(PLATFORM_VALUES), "-f", str(values), "--set", "auth.enabled=true")
    report.expect(
        "with auth.enabled the ingress asks the sign-in proxy",
        ok=signed.returncode == 0 and "nginx.ingress.kubernetes.io/auth-url:" in signed.stdout and "nginx.ingress.kubernetes.io/auth-signin:" in signed.stdout,
        seen=signed.stdout[-1500:] + signed.stderr,
    )
    blank = helm(binary, "template", "fi-bond-prices", str(CHART), "-f", str(values), "--set", "auth.enabled=true")
    report.expect("auth.enabled without its URLs is refused", ok=blank.returncode != 0 and "auth.url is required" in blank.stderr, seen=blank.stderr)
    none = work / "none.json"
    none.write_text(render.helm_values(config.load(), app("none", ()), image="i", release=1).model_dump_json(by_alias=True))
    empty = helm(binary, "template", "fi-bond-prices", str(CHART), "-f", str(PLATFORM_VALUES), "-f", str(none))
    report.expect("an app without a dashboard renders nothing", ok=empty.returncode == 0 and "kind: Deployment" not in empty.stdout, seen=empty.stdout[:500])
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main())
