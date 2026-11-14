import tomllib
from pathlib import Path

import pytest
from packaging.version import Version

from ctzd_cli import assets, checks, config, sync

from .conftest import app_config, make_app


def test_a_fresh_app_has_no_drift_and_records_this_cli(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert sync.drift(tmp_path, config.load(), app) == []
    assert sync.recorded_version(tmp_path) == Version(assets.version())


def test_an_edited_locked_file_is_reported_and_restored(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    (tmp_path / "ruff.toml").write_text("line-length = 80\n")
    assert sync.drift(tmp_path, config.load(), app) == [sync.Drift("ruff.toml", "changed")]
    assert sync.write_build(tmp_path, config.load(), app) == ["ruff.toml"]
    assert sync.drift(tmp_path, config.load(), app) == []


def test_windows_line_endings_are_not_drift(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    path = tmp_path / "Dockerfile"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert sync.drift(tmp_path, config.load(), app) == []


def test_a_deleted_locked_file_is_missing(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    (tmp_path / "Dockerfile").unlink()
    assert sync.Drift("Dockerfile", "missing") in sync.drift(tmp_path, config.load(), app)


def test_guidance_is_not_drift_checked(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    (tmp_path / "AGENTS.md").write_text("edited\n")
    (tmp_path / ".agents" / "skills" / "homemade").mkdir()
    assert sync.drift(tmp_path, config.load(), app) == []


def test_changing_a_managed_pyproject_table_is_drift_but_dependencies_are_not(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    path = tmp_path / "pyproject.toml"
    document = tomllib.loads(path.read_text())
    kit = next(item for item in document["project"]["dependencies"] if item.startswith("ctzd-fi"))
    path.write_text(path.read_text().replace(f'"{kit}"', f'"{kit}", "plotly>=6"'))
    assert sync.drift(tmp_path, config.load(), app) == []
    path.write_text(path.read_text().replace('"ruff==0.17.3"', '"ruff==0.18.0"'))
    assert sync.drift(tmp_path, config.load(), app) == [sync.Drift("pyproject.toml [dependency-groups.dev]", "changed")]
    assert sync.write_build(tmp_path, config.load(), app) == ["pyproject.toml"]
    rewritten = tomllib.loads(path.read_text())
    assert "plotly>=6" in rewritten["project"]["dependencies"]
    assert "ruff==0.17.3" in rewritten["dependency-groups"]["dev"]


def test_guidance_goes_to_agents_and_claude_and_is_gitignored(tmp_path: Path) -> None:
    make_app(tmp_path)
    agents = sorted(path.relative_to(tmp_path / ".agents").as_posix() for path in (tmp_path / ".agents").rglob("*.md"))
    claude = sorted(path.relative_to(tmp_path / ".claude").as_posix() for path in (tmp_path / ".claude").rglob("*.md"))
    assert agents == claude
    assert "skills/ctzd-workflow/SKILL.md" in agents
    ignored = (tmp_path / ".gitignore").read_text().splitlines()
    assert {"/AGENTS.md", "/.agents/", ".claude/"} <= set(ignored)


def test_writing_guidance_adds_the_kits_skill_and_removes_extra_ones(tmp_path: Path) -> None:
    make_app(tmp_path)
    extra = tmp_path / ".agents" / "skills" / "homemade" / "SKILL.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("mine\n")
    kit_skill = {".agents/skills/sbu-fi/SKILL.md": "fi\n", ".claude/skills/sbu-fi/SKILL.md": "fi\n"}
    assert sync.write_guidance(tmp_path, kit_skill)
    assert not extra.exists()
    assert (tmp_path / ".claude" / "skills" / "sbu-fi" / "SKILL.md").read_text() == "fi\n"
    assert not sync.write_guidance(tmp_path, kit_skill)


def test_an_unreadable_recorded_version_counts_as_none(tmp_path: Path) -> None:
    (tmp_path / ".ctzd").mkdir()
    for text, expected in (("0.5.0\n", Version("0.5.0")), ("not a version\n", None), ("", None)):
        (tmp_path / sync.VERSION_FILE).write_text(text)
        assert sync.recorded_version(tmp_path) == expected


def test_ci_refuses_an_app_written_by_another_cli_version(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    (tmp_path / sync.VERSION_FILE).write_text("0.0.1\n")
    target = checks.Target(root=tmp_path, app=app, platform=config.load())
    step = checks.platform_files(target, "ci")
    assert step.state == "failed"
    assert "must run the version the app records" in step.detail


def test_a_desktop_upgrades_an_older_app_and_refuses_a_newer_one(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    target = checks.Target(root=tmp_path, app=app, platform=config.load())
    (tmp_path / sync.VERSION_FILE).write_text("0.0.1\n")
    upgraded = checks.platform_files(target, "local")
    assert upgraded.state == "fixed"
    assert upgraded.detail.startswith("Upgraded")
    (tmp_path / sync.VERSION_FILE).write_text("999.0.0\n")
    refused = checks.platform_files(target, "local")
    assert refused.state == "failed"
    assert "uv tool upgrade ctzd" in refused.detail


@pytest.mark.parametrize("kind", ["dash", "job"])
def test_each_kind_gets_its_own_template(tmp_path: Path, kind: str) -> None:
    make_app(tmp_path, app_config(kind="dash" if kind == "dash" else "job"))
    assert (tmp_path / "app.toml").read_text().count(f'kind = "{kind}"') == 1
