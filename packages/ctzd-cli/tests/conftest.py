from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ctzd_cli import api_models as m
from ctzd_cli import config, releases, scaffold
from ctzd_cli.appconfig import AppConfig, AppSection, Job
from ctzd_cli.errors import CliError


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ctzd's per-user state (login profile, MinGit, release cache) and tokens out of the real home folder."""
    monkeypatch.setenv("CTZD_HOME", str(tmp_path / "ctzd-home"))
    monkeypatch.delenv("CTZD_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("CTZD_PLATFORM_CONFIG", raising=False)


def app_config(*, ui: str = "streamlit", jobs: tuple[Job, ...] = (), sources: tuple[str, ...] = ()) -> AppConfig:
    return AppConfig.model_validate(
        {"app": {"name": "bond-prices", "sbu": "fi", "ui": ui, "owners": ["ann.lee@contoso.com"]}, "sources": {"use": list(sources)}, "jobs": [job.model_dump() for job in jobs]}
    )


def variant(base: m.PlatformRelease, number: int, *, files: dict[str, str] | None = None, **changes: object) -> m.PlatformRelease:
    """`base` renumbered, with some files replaced and manifest fields changed: a later release for tests."""
    data = base.model_dump() | changes | {"release": number, "files": base.files | (files or {})}
    return m.PlatformRelease.model_validate(data)


def make_app(root: Path, app: AppConfig | None = None, *, release: m.PlatformRelease | None = None) -> AppConfig:
    chosen = app or app_config()
    root.mkdir(parents=True, exist_ok=True)
    scaffold.scaffold(root, platform=config.load(), app=chosen, release=release or releases.seed())
    return chosen


@dataclass
class FakePlatform:
    builds: list[m.Build]
    resumed: int = 0
    asked: list[str] = field(default_factory=list)
    paused: bool = False
    job_states: list[m.JobState] = field(default_factory=lambda: ["queued"])
    channel: m.CurrentRelease | None = None
    published: dict[int, m.PlatformRelease] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)

    def me(self) -> m.Me:
        return m.Me(login="ann")

    def create_app(self, request: m.CreateAppRequest) -> m.CreatedApp:
        return m.CreatedApp(repo=f"ctzd-apps/{request.sbu}-{request.name}", clone_url="unused", url="unused")

    def status(self, sbu: str, name: str) -> m.AppStatus:
        return m.AppStatus(sbu=sbu, name=name, url=f"https://{sbu}-{name}.ctzd.contoso.com", release=4, paused=self.paused, dags={}, last_build=None)

    def resume(self, sbu: str, name: str) -> None:
        del sbu, name
        self.resumed += 1
        self.events.append("resume")

    def build_for_commit(self, sbu: str, name: str, commit: str) -> m.Build:
        del sbu, name
        self.asked.append(commit)
        return self.builds.pop(0) if len(self.builds) > 1 else self.builds[0]

    def screenshot(self, sbu: str, name: str, number: int) -> bytes:
        del sbu, name, number
        return b"\x89PNG fake"

    def rollback(self, sbu: str, name: str, release: int | None) -> m.RollbackAccepted:
        raise NotImplementedError

    def run_job(self, sbu: str, name: str, job: str) -> m.JobRunCreated:
        return m.JobRunCreated(dag_id=f"{sbu}__{name.replace('-', '_')}__{job}", run_id="manual__1", url="https://airflow.ctzd.contoso.com/dags/x/runs/manual__1")

    def job_run(self, sbu: str, name: str, job: str, run_id: str) -> m.JobRunState:
        state = self.job_states.pop(0) if len(self.job_states) > 1 else self.job_states[0]
        return m.JobRunState(dag_id=f"{sbu}__{name.replace('-', '_')}__{job}", run_id=run_id, state=state, url="https://airflow.ctzd.contoso.com/dags/x/runs/manual__1")

    def sources(self) -> m.SourceList:
        return m.SourceList(sources=("fi_pricing",))

    def logs(self, sbu: str, name: str, *, job: str | None, tail: int) -> m.LogText:
        raise NotImplementedError

    def env(self, sbu: str, name: str, sources: tuple[str, ...]) -> m.EnvVars:
        raise NotImplementedError

    def delete(self, sbu: str, name: str) -> None:
        raise NotImplementedError

    def current_release(self, sbu: str | None, name: str | None) -> m.CurrentRelease:
        del sbu, name
        if self.channel is None:
            msg = "The ctzd platform could not be reached."
            raise CliError(msg)
        return self.channel

    def release(self, number: int) -> m.PlatformRelease:
        self.events.append(f"release {number}")
        if number not in self.published:
            msg = f"There is no platform release {number}."
            raise CliError(msg)
        return self.published[number]


__all__ = ["AppSection", "FakePlatform", "app_config", "make_app", "variant"]
