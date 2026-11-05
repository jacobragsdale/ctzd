import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from ctzd_cli import commands, main

from .conftest import make_app

_OUTPUT: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def run(argv: list[str], cwd: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    ctx = commands.Context(cwd=cwd, platform_factory=lambda: pytest.fail("no platform call expected"), token=lambda: "t")
    code = main.main([*argv, "--json"], ctx=ctx)
    return code, _OUTPUT.validate_json(capsys.readouterr().out)


def test_add_job_writes_the_module_and_app_toml(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_app(tmp_path)
    code, out = run(["add", "job", "nightly", "--schedule", "0 2 * * *"], tmp_path, capsys)
    assert code == 0
    assert out["files"] == ["src/app/jobs/nightly.py", "tests/test_job_nightly.py"]
    assert 'name = "nightly"' in (tmp_path / "app.toml").read_text()


def test_a_bad_schedule_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_app(tmp_path)
    code, out = run(["add", "job", "nightly", "--schedule", "daily"], tmp_path, capsys)
    assert code == main.EXIT_USAGE
    assert out["ok"] is False


def test_outside_an_app_the_error_says_what_to_do(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(["sync"], tmp_path, capsys)
    assert code == main.EXIT_FAILED
    assert out["next"] == ["Run ctzd from the app's folder, or create one with `ctzd new`."]


def test_delete_needs_confirmation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_app(tmp_path)
    code, out = run(["delete"], tmp_path, capsys)
    assert code == main.EXIT_USAGE
    assert "--yes" in str(out["next"])


def test_ci_render_writes_both_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_app(tmp_path)
    code, _out = run(["ci", "render", "--image", "acr/fi-bond-prices:abc", "--out", str(tmp_path / "out")], tmp_path, capsys)
    assert code == 0
    assert json.loads((tmp_path / "out" / "helm-values.json").read_text())["image"] == "acr/fi-bond-prices:abc"
    assert json.loads((tmp_path / "out" / "airflow-spec.json").read_text())["jobs"] == []


@pytest.mark.parametrize(("text", "number"), [("v12", 12), ("12", 12), (None, None)])
def test_release_arguments(text: str | None, number: int | None) -> None:
    assert commands.parse_release(text) == number


def test_dotenv_values_survive_quotes() -> None:
    assert commands._dotenv_value('{"kind":"rest"}') == """'{"kind":"rest"}'"""  # noqa: SLF001 - the quoting rule is the unit under test.
    assert commands._dotenv_value("it's") == '"it\'s"'  # noqa: SLF001 - the quoting rule is the unit under test.
