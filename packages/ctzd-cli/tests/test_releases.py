from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctzd_cli import api_models as m
from ctzd_cli import assets, checks, commands, config, releases, setup_checks, sync
from ctzd_cli.errors import CliError

from .conftest import FakePlatform, app_config, make_app, variant

SEED = releases.seed()
MOMENT = datetime(2026, 11, 6, 9, tzinfo=UTC)
NEW_PINS = SEED.python.model_copy(update={"dev": (*SEED.python.dev, "mypy==2.0")}).model_dump()


def source(platform: FakePlatform | None) -> releases.ReleaseSource:
    return releases.ReleaseSource(None if platform is None else lambda: platform)


def context(root: Path, platform: FakePlatform | None, *, now: datetime = MOMENT) -> commands.Context:
    fake = platform or FakePlatform([m.Build(number=1, state="succeeded")])
    return commands.Context(cwd=root, platform_factory=lambda: fake, token=lambda: "token", now=lambda: now, releases=source(platform))


def target(root: Path, platform: FakePlatform | None) -> checks.Target:
    return checks.Target(root=root, app=app_config(), platform=config.load(), releases=source(platform))


def online(*published: m.PlatformRelease, current: int, cli: str | None = None) -> FakePlatform:
    return FakePlatform([m.Build(number=1, state="succeeded")], channel=m.CurrentRelease(release=current, channel="general", cli=cli), published={item.release: item for item in published})


# ---------------------------------------------------------------- the release itself


def test_the_seed_is_the_bundled_folder() -> None:
    assert SEED.release == 1
    assert "locked/Dockerfile" in SEED.files
    assert "guidance/AGENTS.md" in SEED.files
    assert "release.toml" not in SEED.files
    assert set(SEED.ui) == {"streamlit", "dash", "none"}


def test_a_folder_without_a_manifest_or_with_a_bad_one_is_refused() -> None:
    with pytest.raises(CliError, match=r"no release\.toml"):
        releases.from_files({"locked/Dockerfile": "FROM x"})
    with pytest.raises(CliError, match="invalid"):
        releases.from_files({"release.toml": "release = 'one'"})


def test_files_outside_the_release_folders_are_refused() -> None:
    with pytest.raises(ValueError, match="outside"):
        variant(SEED, 2, files={"locked/../../etc/passwd": "x"})


def test_a_release_that_needs_a_newer_cli_stops_the_command() -> None:
    with pytest.raises(CliError, match=r"needs ctzd 99\.0") as caught:
        releases.require_usable(variant(SEED, 2, requires_cli="99.0"))
    assert caught.value.hint is not None
    assert "uv tool upgrade ctzd" in caught.value.hint
    with pytest.raises(CliError):
        releases.require_usable(variant(SEED, 2, format=releases.FORMAT + 1))
    assert releases.require_usable(SEED) is SEED


def test_a_newer_published_cli_only_hints() -> None:
    assert releases.upgrade_hint(m.CurrentRelease(release=1, channel="general", cli="99.0")) is not None
    assert releases.upgrade_hint(m.CurrentRelease(release=1, channel="general", cli=assets.version())) is None
    assert releases.upgrade_hint(m.CurrentRelease(release=1, channel="general")) is None


# ---------------------------------------------------------------- where releases come from


def test_a_fetched_release_is_cached_and_not_fetched_again() -> None:
    later = variant(SEED, 2)
    platform = online(later, current=2)
    assert source(platform).get(2) == later
    assert source(platform).get(2) == later
    assert platform.events == ["release 2"]
    assert source(None).newest_local() == later


def test_without_the_platform_the_seed_serves_its_own_number_only() -> None:
    offline = source(None)
    assert offline.current("fi", "bond-prices") is None
    assert offline.unreachable is None
    assert offline.get(SEED.release) == SEED
    with pytest.raises(CliError, match="not on this computer"):
        offline.get(7)


def test_an_unreachable_platform_is_remembered_with_its_reason() -> None:
    down = FakePlatform([m.Build(number=1, state="succeeded")])
    found = source(down)
    assert found.current("fi", "bond-prices") is None
    assert found.unreachable == "The ctzd platform could not be reached."
    assert found.for_app("fi", "bond-prices") == SEED


def test_a_damaged_cache_entry_is_fetched_again() -> None:
    later = variant(SEED, 2)
    platform = online(later, current=2)
    source(platform).get(2)
    (releases.cache_dir() / "2.json").write_text("{")
    assert source(platform).get(2) == later
    assert platform.events == ["release 2", "release 2"]


# ---------------------------------------------------------------- build files move with the channel


def test_a_guidance_only_release_leaves_the_app_on_its_release(tmp_path: Path) -> None:
    make_app(tmp_path)
    advice = variant(SEED, 2, files={"skills/common/ctzd-data/SKILL.md": "new advice\n"})
    step = checks.platform_files(target(tmp_path, online(advice, current=2)), "local")
    assert step == checks.Step("platform files", "passed")
    assert sync.recorded_release(tmp_path) == SEED.release


def test_a_release_with_new_build_files_upgrades_the_app_and_names_the_paths(tmp_path: Path) -> None:
    make_app(tmp_path)
    later = variant(SEED, 2, files={"locked/ruff.toml": "line-length = 120\n"}, python=NEW_PINS)
    step = checks.platform_files(target(tmp_path, online(later, current=2)), "local")
    assert step.state == "fixed"
    assert step.detail.startswith("Upgraded platform files to platform release 2")
    assert set(step.paths) == {".ctzd/version", "pyproject.toml", "ruff.toml"}
    assert sync.recorded_release(tmp_path) == 2
    assert sync.drift(tmp_path, config.load(), later) == []


def test_a_reverted_channel_moves_the_app_back(tmp_path: Path) -> None:
    later = variant(SEED, 2, files={"locked/ruff.toml": "line-length = 120\n"})
    make_app(tmp_path, release=later)
    step = checks.platform_files(target(tmp_path, online(SEED, later, current=1)), "local")
    assert step.detail.startswith("Moved platform files to platform release 1")
    assert sync.recorded_release(tmp_path) == 1


def test_offline_an_edited_file_is_restored_to_the_recorded_release(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    step = checks.platform_files(target(tmp_path, None), "local")
    assert step.detail == "Restored platform files to platform release 1: Dockerfile."
    assert step.paths == ("Dockerfile",)


def test_offline_a_recorded_release_that_is_not_here_fails_without_touching_files(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / sync.VERSION_FILE).write_text("5\n")
    before = (tmp_path / "Dockerfile").read_text()
    step = checks.platform_files(target(tmp_path, None), "local")
    assert step.state == "failed"
    assert "not on this computer" in step.detail
    assert (tmp_path / "Dockerfile").read_text() == before


def test_ci_checks_against_the_recorded_release_and_never_writes(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / "ruff.toml").write_text("line-length = 80\n")
    later = variant(SEED, 2, files={"locked/ruff.toml": "line-length = 80\n"})
    step = checks.platform_files(target(tmp_path, online(SEED, later, current=2)), "ci")
    assert step.state == "failed"
    assert "ruff.toml (changed)" in step.detail
    assert (tmp_path / "ruff.toml").read_text() == "line-length = 80\n"


def test_ci_fails_an_app_that_records_no_release(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / sync.VERSION_FILE).unlink()
    assert "records no platform release" in checks.platform_files(target(tmp_path, None), "ci").detail


def test_ci_fails_when_ctzd_core_is_older_than_the_release_needs(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / "uv.lock").write_text('version = 1\n[[package]]\nname = "ctzd-core"\nversion = "0.1.0"\n')
    newer = variant(SEED, 2, min_ctzd_core="0.3.0")
    (tmp_path / sync.VERSION_FILE).write_text("2\n")
    step = checks.platform_files(target(tmp_path, online(newer, current=2)), "ci")
    assert "ctzd-core 0.1.0" in step.detail
    assert checks.locked_version(tmp_path, "ctzd-core") is not None
    assert checks.locked_version(tmp_path, "pandas") is None


# ---------------------------------------------------------------- guidance before every command


def test_guidance_follows_the_channel_and_is_stamped(tmp_path: Path) -> None:
    make_app(tmp_path)
    advice = variant(SEED, 2, files={"guidance/AGENTS.md": "# New instructions\n"})
    hints = commands.refresh_guidance(context(tmp_path, online(advice, current=2, cli="99.0")), tmp_path, app_config())
    assert (tmp_path / "AGENTS.md").read_text() == "# New instructions\n"
    assert sync.read_stamp(tmp_path) == sync.GuidanceStamp(release=2, checked_at=MOMENT)
    assert len(hints) == 1
    assert "uv tool upgrade ctzd" in hints[0]


def test_fresh_guidance_does_not_ask_the_platform(tmp_path: Path) -> None:
    make_app(tmp_path)
    sync.write_stamp(tmp_path, 1, MOMENT)
    platform = online(variant(SEED, 2), current=2)
    commands.refresh_guidance(context(tmp_path, platform, now=MOMENT + timedelta(minutes=1)), tmp_path, app_config())
    assert platform.events == []
    commands.refresh_guidance(context(tmp_path, platform, now=MOMENT + sync.GUIDANCE_REFRESH), tmp_path, app_config())
    assert platform.events == ["release 2"]


def test_without_the_platform_existing_guidance_is_kept_and_the_reason_given(tmp_path: Path) -> None:
    make_app(tmp_path)
    sync.write_stamp(tmp_path, 1, MOMENT - timedelta(days=1))
    (tmp_path / "AGENTS.md").write_text("kept\n")
    hints = commands.refresh_guidance(context(tmp_path, FakePlatform([m.Build(number=1, state="succeeded")])), tmp_path, app_config())
    assert (tmp_path / "AGENTS.md").read_text() == "kept\n"
    assert hints == ["The agent guidance could not be refreshed (The ctzd platform could not be reached.); it may be out of date."]


def test_missing_guidance_is_written_from_the_newest_local_release(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    assert commands.refresh_guidance(context(tmp_path, None), tmp_path, app_config()) == []
    assert (tmp_path / "AGENTS.md").is_file()


def test_prepare_refreshes_only_inside_an_app_and_only_for_app_commands(tmp_path: Path) -> None:
    make_app(tmp_path)
    advice = variant(SEED, 2, files={"guidance/AGENTS.md": "# New\n"})
    platform = online(advice, current=2)
    assert commands.prepare(context(tmp_path, platform), "setup") == []
    assert platform.events == []
    commands.prepare(context(tmp_path, platform), "status")
    assert (tmp_path / "AGENTS.md").read_text() == "# New\n"
    assert commands.prepare(context(tmp_path / "elsewhere", platform), "status") == []


def test_a_channel_on_a_release_too_new_for_this_cli_stops_every_app_command(tmp_path: Path) -> None:
    make_app(tmp_path)
    platform = online(variant(SEED, 2, requires_cli="99.0"), current=2)
    with pytest.raises(CliError, match=r"needs ctzd 99\.0"):
        commands.prepare(context(tmp_path, platform), "check")


def test_setup_hands_over_the_channels_start_guide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ready = [setup_checks.Check("uv", "ok", "uv"), setup_checks.Check("login", "ok", "ann")]
    monkeypatch.setattr(setup_checks, "run", lambda _cwd, **_client: ready)
    guide = variant(SEED, 2, files={"start/GUIDE.md": "# Start here\n"})
    outcome = commands.setup(context(tmp_path, online(guide, current=2)))
    assert outcome.ok
    assert outcome.data["guide"] == "# Start here\n"
    # Offline, the newest release on this computer is the one just fetched.
    assert commands.setup(context(tmp_path, None)).data["guide"] == "# Start here\n"


def test_setup_with_something_missing_says_what_to_fix_and_gives_no_guide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = [setup_checks.Check("login", "missing", "Run `ctzd login --start`.")]
    monkeypatch.setattr(setup_checks, "run", lambda _cwd, **_client: missing)
    outcome = commands.setup(context(tmp_path, None))
    assert not outcome.ok
    assert outcome.next_steps == ["Run `ctzd login --start`."]
    assert "guide" not in outcome.data


def test_cached_releases_this_cli_cannot_use_are_not_offered_offline() -> None:
    too_new = variant(SEED, 9, requires_cli="99.0")
    source(online(too_new, current=9)).get(9)
    assert source(None).newest_local() == SEED


def test_offline_existing_guidance_is_kept_even_without_a_stamp(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / "AGENTS.md").write_text("kept\n")
    (tmp_path / sync.GUIDANCE_STAMP).unlink(missing_ok=True)
    commands.refresh_guidance(context(tmp_path, None), tmp_path, app_config())
    assert (tmp_path / "AGENTS.md").read_text() == "kept\n"


def test_a_release_the_platform_does_not_have_is_an_error_not_an_outage() -> None:
    found = source(online(current=1))
    with pytest.raises(CliError, match="no platform release 7"):
        found.get(7)
    assert found.unreachable is None
    assert found.get(SEED.release) == SEED
