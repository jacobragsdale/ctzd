import json
from pathlib import Path

import pytest

from ctzd_cli import commands, main

from .conftest import make_app, run


def test_outside_an_app_the_error_says_what_to_do(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(["sync"], tmp_path, capsys)
    assert code == main.EXIT_FAILED
    assert out["next"] == ["Run ctzd from the app's folder, or create one with `ctzd new`."]


def test_ci_render_writes_both_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_app(tmp_path)
    code, _out = run(["ci", "render", "--image", "acr/fi-bond-prices:abc", "--out", str(tmp_path / "out")], tmp_path, capsys)
    assert code == 0
    assert json.loads((tmp_path / "out" / "helm-values.json").read_text())["image"] == "acr/fi-bond-prices:abc"
    assert json.loads((tmp_path / "out" / "airflow-spec.json").read_text())["jobs"] == []


def test_new_without_offline_says_the_github_side_is_not_built(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(["new", "bond-prices", "--sbu", "fi", "--owner", "ann.lee@contoso.com"], tmp_path, capsys)
    assert code == main.EXIT_USAGE
    assert "not built yet" in str(out["summary"])
    assert not (tmp_path / "bond-prices").exists()


def test_a_job_app_has_no_dashboard(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from .conftest import app_config  # noqa: PLC0415 - the one test that needs a job app

    make_app(tmp_path, app_config(kind="job"))
    code, out = run(["dev"], tmp_path, capsys)
    assert code == main.EXIT_USAGE
    assert "no dashboard" in str(out["summary"])


@pytest.mark.parametrize("argv", [["--json", "sync"], ["sync", "--json"]])
def test_json_works_before_or_after_the_command(tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    make_app(tmp_path)
    main.main(argv, ctx=commands.Context(cwd=tmp_path))
    assert json.loads(capsys.readouterr().out)["command"] == "sync"
