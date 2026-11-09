"""Breaking platform releases: they reach an app only once the app passes on them, or from their deadline."""

import datetime as dt
from pathlib import Path

import pytest

from ctzd_cli import api_models as m
from ctzd_cli import checks, config, releases, sync

from .conftest import FakePlatform, app_config, make_app, variant

SEED = releases.seed()
LATER = SEED.release + 1
MOMENT = dt.datetime(2026, 11, 9, 9, tzinfo=dt.UTC)


def online(*published: m.PlatformRelease, current: int) -> FakePlatform:
    return FakePlatform([m.Build(number=1, state="succeeded")], channel=m.CurrentRelease(release=current, channel="general"), published={item.release: item for item in published})


def target(root: Path, platform: FakePlatform) -> checks.Target:
    return checks.Target(root=root, app=app_config(), platform=config.load(), releases=releases.ReleaseSource(lambda: platform))


STRICTER = {"locked/ruff.toml": SEED.files["locked/ruff.toml"] + "# stricter\n"}


def test_a_release_that_changes_a_checker_or_its_pins_is_breaking_whatever_its_manifest_says() -> None:
    platform = config.load()
    assert checks.is_breaking(platform, SEED, variant(SEED, LATER, files=STRICTER), "react")
    assert checks.is_breaking(platform, SEED, variant(SEED, LATER, python=SEED.python.model_copy(update={"dev": ("ruff==9.9.9",)}).model_dump()), "react")
    assert checks.is_breaking(platform, SEED, variant(SEED, LATER, breaking=True), "react")
    assert not checks.is_breaking(platform, SEED, variant(SEED, LATER, files={"locked/Dockerfile": "FROM x\n"}), "react")


def _running(monkeypatch: pytest.MonkeyPatch, *, fails_on: int | None) -> list[int | None]:
    """Replace the checks themselves: they fail while the app's build files are those of release `fails_on`."""
    seen: list[int | None] = []

    def run(target: checks.Target, mode: checks.Mode, *, fast: bool, first: checks.Step) -> list[checks.Step]:
        del mode, fast
        recorded = sync.recorded_release(target.root)
        seen.append(recorded)
        return [first, checks.Step("lint", "failed" if recorded == fails_on else "passed")]

    monkeypatch.setattr(checks, "_run", run)
    return seen


def test_an_app_that_fails_a_breaking_release_stays_on_its_own_with_a_warning_and_the_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_app(tmp_path)
    before = (tmp_path / "ruff.toml").read_text()
    seen = _running(monkeypatch, fails_on=LATER)
    later = variant(SEED, LATER, files=STRICTER, deadline=dt.date(2027, 1, 31))
    steps = checks.run_all(target(tmp_path, online(later, current=LATER)), mode="local", fast=False, now=MOMENT)
    assert seen == [LATER, SEED.release]
    assert [step.state for step in steps] == ["passed", "passed"]
    assert sync.recorded_release(tmp_path) == SEED.release
    assert (tmp_path / "ruff.toml").read_text() == before
    assert steps[0].warning is not None
    assert f"stays on platform release {SEED.release}" in steps[0].warning
    assert "(lint)" in steps[0].warning
    assert "31 January 2027" in steps[0].warning

    # The next check that day does not try it again.
    checks.run_all(target(tmp_path, online(later, current=LATER)), mode="local", fast=False, now=MOMENT + dt.timedelta(hours=2))
    assert seen == [LATER, SEED.release, SEED.release]


def test_an_app_that_passes_a_breaking_release_keeps_it_after_one_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_app(tmp_path)
    seen = _running(monkeypatch, fails_on=None)
    later = variant(SEED, LATER, files=STRICTER)
    steps = checks.run_all(target(tmp_path, online(later, current=LATER)), mode="local", fast=False, now=MOMENT)
    assert seen == [LATER]
    assert steps[0].state == "fixed"
    assert sync.recorded_release(tmp_path) == LATER


def test_a_breaking_release_applies_from_its_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_app(tmp_path)
    seen = _running(monkeypatch, fails_on=LATER)
    later = variant(SEED, LATER, files=STRICTER, deadline=MOMENT.date())
    steps = checks.run_all(target(tmp_path, online(later, current=LATER)), mode="local", fast=False, now=MOMENT)
    assert seen == [LATER]
    assert steps[-1].state == "failed"
    assert sync.recorded_release(tmp_path) == LATER


def test_a_fast_check_never_moves_to_a_breaking_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_app(tmp_path)
    seen = _running(monkeypatch, fails_on=None)
    later = variant(SEED, LATER, files=STRICTER)
    steps = checks.run_all(target(tmp_path, online(later, current=LATER)), mode="local", fast=True, now=MOMENT)
    assert seen == [SEED.release]
    assert steps[0].warning is not None
    assert "without --fast" in steps[0].warning
