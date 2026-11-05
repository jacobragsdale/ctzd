import tomllib
from pathlib import Path

from ctzd_cli import config, sync

from .conftest import app_config, make_app


def test_a_fresh_app_has_no_drift(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert sync.drift(tmp_path, app, config.load(), "0.1.0") == []


def test_an_edited_locked_file_is_reported_and_restored(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    (tmp_path / "ruff.toml").write_text("line-length = 80\n")
    assert sync.drift(tmp_path, app, config.load(), "0.1.0") == [sync.Drift("ruff.toml", "changed")]
    assert sync.write(tmp_path, app, config.load(), "0.1.0") == ["ruff.toml"]
    assert sync.drift(tmp_path, app, config.load(), "0.1.0") == []


def test_windows_line_endings_are_not_drift(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    path = tmp_path / "Dockerfile"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert sync.drift(tmp_path, app, config.load(), "0.1.0") == []


def test_a_deleted_locked_file_is_missing(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    (tmp_path / "Jenkinsfile").unlink()
    assert sync.Drift("Jenkinsfile", "missing") in sync.drift(tmp_path, app, config.load(), "0.1.0")


def test_a_skill_added_by_hand_is_unexpected_and_removed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    extra = tmp_path / ".agents" / "skills" / "homemade" / "SKILL.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("---\nname: homemade\n---\n")
    assert sync.Drift(".agents/skills/homemade/SKILL.md", "unexpected") in sync.drift(tmp_path, app, config.load(), "0.1.0")
    sync.write(tmp_path, app, config.load(), "0.1.0")
    assert not extra.parent.exists()


def test_changing_a_managed_pyproject_table_is_drift_but_dependencies_are_not(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    path = tmp_path / "pyproject.toml"
    text = path.read_text()
    path.write_text(text.replace('dependencies = ["ctzd-fi>=0.1", "streamlit>=1.66"]', 'dependencies = ["ctzd-fi>=0.1", "plotly>=6", "streamlit>=1.66"]'))
    assert sync.drift(tmp_path, app, config.load(), "0.1.0") == []
    path.write_text(path.read_text().replace('"ruff==0.17.3"', '"ruff==0.18.0"'))
    assert sync.drift(tmp_path, app, config.load(), "0.1.0") == [sync.Drift("pyproject.toml [dependency-groups.dev]", "changed")]


def test_sync_rewrites_managed_tables_and_keeps_dependencies(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    path = tmp_path / "pyproject.toml"
    path.write_text(path.read_text().replace('"ruff==0.17.3"', '"ruff==0.1.0"').replace('"streamlit>=1.66"]', '"streamlit>=1.66", "plotly>=6"]'))
    sync.write(tmp_path, app, config.load(), "0.1.0")
    pyproject = tomllib.loads(path.read_text())
    assert "plotly>=6" in pyproject["project"]["dependencies"]
    assert "ruff==0.17.3" in pyproject["dependency-groups"]["dev"]


def test_claude_gets_a_copy_of_the_skills(tmp_path: Path) -> None:
    make_app(tmp_path)
    agents = sorted(path.relative_to(tmp_path / ".agents").as_posix() for path in (tmp_path / ".agents").rglob("*.md"))
    claude = sorted(path.relative_to(tmp_path / ".claude").as_posix() for path in (tmp_path / ".claude").rglob("*.md"))
    assert agents == claude


def test_env_example_lists_the_apps_sources(tmp_path: Path) -> None:
    make_app(tmp_path, app_config(sources=("fi_pricing",)))
    assert "CTZD_SOURCE_FI_PRICING=" in (tmp_path / ".env.example").read_text()


def test_version_comparison() -> None:
    assert sync.compare_versions("0.1.0", "0.1.0") == "same"
    assert sync.compare_versions("0.1.0", "0.2.0") == "behind"
    assert sync.compare_versions("0.10.0", "0.9.0") == "ahead"
    assert sync.compare_versions(None, "0.1.0") == "behind"
