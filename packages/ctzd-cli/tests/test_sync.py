import tomllib
from datetime import UTC, datetime
from pathlib import Path

from ctzd_cli import config, releases, sync

from .conftest import app_config, make_app, variant

SEED = releases.seed()
LATER = SEED.release + 1  # a platform release published after the bundled one


def test_a_fresh_app_has_no_drift_and_records_its_release(tmp_path: Path) -> None:
    make_app(tmp_path)
    assert sync.drift(tmp_path, config.load(), SEED, "react") == []
    assert sync.recorded_release(tmp_path) == SEED.release


def test_an_edited_locked_file_is_reported_and_restored(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / "ruff.toml").write_text("line-length = 80\n")
    assert sync.drift(tmp_path, config.load(), SEED, "react") == [sync.Drift("ruff.toml", "changed")]
    assert sync.write_build(tmp_path, config.load(), SEED, "react") == ["ruff.toml"]
    assert sync.drift(tmp_path, config.load(), SEED, "react") == []


def test_windows_line_endings_are_not_drift(tmp_path: Path) -> None:
    make_app(tmp_path)
    path = tmp_path / "Dockerfile"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert sync.drift(tmp_path, config.load(), SEED, "react") == []


def test_a_deleted_locked_file_is_missing(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / "Jenkinsfile").unlink()
    assert sync.Drift("Jenkinsfile", "missing") in sync.drift(tmp_path, config.load(), SEED, "react")


def test_guidance_is_not_drift_checked(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / "AGENTS.md").write_text("edited\n")
    (tmp_path / ".agents" / "skills" / "homemade").mkdir()
    assert sync.drift(tmp_path, config.load(), SEED, "react") == []


def test_changing_a_managed_pyproject_table_is_drift_but_dependencies_are_not(tmp_path: Path) -> None:
    make_app(tmp_path)
    path = tmp_path / "pyproject.toml"
    text = path.read_text()
    path.write_text(text.replace('"ctzd-fi>=0.1"]', '"ctzd-fi>=0.1", "plotly>=6"]'))
    assert sync.drift(tmp_path, config.load(), SEED, "react") == []
    path.write_text(path.read_text().replace('"ruff==0.17.3"', '"ruff==0.18.0"'))
    assert sync.drift(tmp_path, config.load(), SEED, "react") == [sync.Drift("pyproject.toml [dependency-groups.dev]", "changed")]


def test_writing_build_files_rewrites_managed_tables_and_keeps_dependencies(tmp_path: Path) -> None:
    make_app(tmp_path)
    path = tmp_path / "pyproject.toml"
    path.write_text(path.read_text().replace('"ruff==0.17.3"', '"ruff==0.1.0"').replace('"ctzd-fi>=0.1"]', '"ctzd-fi>=0.1", "plotly>=6"]'))
    assert sync.write_build(tmp_path, config.load(), SEED, "react") == ["pyproject.toml"]
    pyproject = tomllib.loads(path.read_text())
    assert "plotly>=6" in pyproject["project"]["dependencies"]
    assert "ruff==0.17.3" in pyproject["dependency-groups"]["dev"]


def test_a_later_release_with_other_tool_pins_is_a_different_build(tmp_path: Path) -> None:
    del tmp_path
    pins = SEED.python.model_copy(update={"dev": (*SEED.python.dev, "mypy==2.0")})
    assert not sync.same_build(config.load(), SEED, variant(SEED, LATER, python=pins.model_dump()), "react")
    assert sync.same_build(config.load(), SEED, variant(SEED, LATER, files={"skills/common/ctzd-data/SKILL.md": "new advice"}), "react")


def test_guidance_goes_to_agents_and_claude_and_is_gitignored(tmp_path: Path) -> None:
    make_app(tmp_path)
    agents = sorted(path.relative_to(tmp_path / ".agents").as_posix() for path in (tmp_path / ".agents").rglob("*.md"))
    claude = sorted(path.relative_to(tmp_path / ".claude").as_posix() for path in (tmp_path / ".claude").rglob("*.md"))
    assert agents == claude
    assert "skills/sbu-fi/SKILL.md" in agents
    assert (tmp_path / "AGENTS.md").read_text().startswith("# Instructions for AI agents")
    ignored = (tmp_path / ".gitignore").read_text().splitlines()
    assert {"/AGENTS.md", "/.agents/", ".claude/", ".ctzd/guidance.json"} <= set(ignored)


def test_writing_guidance_replaces_stale_and_removes_extra_skills(tmp_path: Path) -> None:
    make_app(tmp_path)
    extra = tmp_path / ".agents" / "skills" / "homemade" / "SKILL.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("mine\n")
    later = variant(SEED, LATER, files={"skills/common/ctzd-data/SKILL.md": "new advice\n"})
    assert sync.write_guidance(tmp_path, later, "fi")
    assert not extra.exists()
    assert (tmp_path / ".claude" / "skills" / "ctzd-data" / "SKILL.md").read_text() == "new advice\n"
    assert not sync.write_guidance(tmp_path, later, "fi")


def test_env_example_lists_the_apps_sources(tmp_path: Path) -> None:
    make_app(tmp_path, app_config(sources=("fi_pricing",)))
    assert "CTZD_SOURCE_FI_PRICING=" in (tmp_path / ".env.example").read_text()


def test_the_guidance_stamp_round_trips_and_a_damaged_one_is_ignored(tmp_path: Path) -> None:
    moment = datetime(2026, 11, 6, 9, tzinfo=UTC)
    sync.write_stamp(tmp_path, 3, moment)
    assert sync.read_stamp(tmp_path) == sync.GuidanceStamp(release=3, checked_at=moment)
    (tmp_path / sync.GUIDANCE_STAMP).write_text("{")
    assert sync.read_stamp(tmp_path) is None


def test_an_unreadable_recorded_release_counts_as_none(tmp_path: Path) -> None:
    (tmp_path / ".ctzd").mkdir()
    for text, expected in (("4\n", 4), ("0.1.0\n", None), ("0\n", None), ("", None)):
        (tmp_path / sync.VERSION_FILE).write_text(text)
        assert sync.recorded_release(tmp_path) == expected
