from pathlib import Path

import pytest
from pydantic import TypeAdapter

from ctzd_cli import commands, config, main, scaffold
from ctzd_cli.appconfig import AppConfig, Job, Kind


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ctzd's per-user state (login profile, MinGit) and tokens out of the real home folder."""
    monkeypatch.setenv("CTZD_HOME", str(tmp_path / "ctzd-home"))
    monkeypatch.delenv("CTZD_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("CTZD_PLATFORM_CONFIG", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)


_OUTPUT: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def run(argv: list[str], cwd: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = main.main([*argv, "--json"], ctx=commands.Context(cwd=cwd, token=lambda: "t"))
    return code, _OUTPUT.validate_json(capsys.readouterr().out)


def app_config(*, kind: Kind = "dash", jobs: tuple[Job, ...] = (), sources: tuple[str, ...] = ()) -> AppConfig:
    return AppConfig.model_validate(
        {"app": {"name": "bond-prices", "sbu": "fi", "kind": kind, "owners": ["ann.lee@contoso.com"]}, "sources": {"use": list(sources)}, "jobs": [job.model_dump() for job in jobs]}
    )


def make_app(root: Path, app: AppConfig | None = None) -> AppConfig:
    """An app's files as `ctzd new` writes them, without installing its packages."""
    chosen = app or app_config()
    root.mkdir(parents=True, exist_ok=True)
    scaffold.scaffold(root, platform=config.load(), app=chosen)
    return chosen
