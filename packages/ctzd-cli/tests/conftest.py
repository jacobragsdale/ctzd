from pathlib import Path

import pytest

from ctzd_cli import config, scaffold
from ctzd_cli.appconfig import AppConfig, AppSection, Job


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ctzd's per-user state (login profile, MinGit) and tokens out of the real home folder."""
    monkeypatch.setenv("CTZD_HOME", str(tmp_path / "ctzd-home"))
    monkeypatch.delenv("CTZD_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("CTZD_PLATFORM_CONFIG", raising=False)


def app_config(*, ui: str = "streamlit", jobs: tuple[Job, ...] = (), sources: tuple[str, ...] = ()) -> AppConfig:
    return AppConfig.model_validate(
        {"app": {"name": "bond-prices", "sbu": "fi", "ui": ui, "owners": ["ann.lee@contoso.com"]}, "sources": {"use": list(sources)}, "jobs": [job.model_dump() for job in jobs]}
    )


def make_app(root: Path, app: AppConfig | None = None, *, version: str = "0.1.0") -> AppConfig:
    chosen = app or app_config()
    root.mkdir(parents=True, exist_ok=True)
    scaffold.scaffold(root, platform=config.load(), app=chosen, version=version)
    return chosen


__all__ = ["AppSection", "app_config", "make_app"]
