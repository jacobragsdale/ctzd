"""The versions Jenkins pins, against the CLI and chart this repository builds."""

import json
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
JENKINS = REPO / "jenkins"


def test_jenkins_pins_the_cli_and_chart_this_repository_builds() -> None:
    pins = json.loads((JENKINS / "resources" / "ctzd" / "versions.json").read_text(encoding="utf-8"))
    cli = tomllib.loads((REPO / "packages" / "ctzd-cli" / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    chart = re.search(r"^version:\s*(\S+)$", (REPO / "deploy" / "helm" / "ctzd-app" / "Chart.yaml").read_text(encoding="utf-8"), re.MULTILINE)
    assert chart is not None
    assert pins == {"cli": cli, "chart": chart.group(1)}


def test_no_job_pins_a_version_of_its_own() -> None:
    jobs = [JENKINS / "vars" / "ctzdApp.groovy", *(JENKINS / "jobs").glob("*.Jenkinsfile")]
    for job in jobs:
        text = job.read_text(encoding="utf-8")
        assert not re.search(r"""(Version|VERSION)\s*[:=]\s*['"]\d""", text), f"{job.name} pins a version; use ctzdVersions()"
