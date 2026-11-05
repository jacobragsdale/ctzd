"""Write a new app's starter files."""

from pathlib import Path

import tomlkit

from ctzd_cli import assets, sync
from ctzd_cli.appconfig import APP_FILE, AppConfig, Job, render
from ctzd_cli.config import PlatformConfig


def title_for(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("-"))


def initial_pyproject(platform: PlatformConfig, app: AppConfig) -> str:
    document = tomlkit.document()
    project = tomlkit.table()
    project.add("name", platform.repo_name(app.app.sbu, app.app.name))
    project.add("version", "0.0.0")
    project.add("requires-python", platform.python.requires)
    dependencies = [platform.sbu(app.app.sbu).library, *platform.ui[app.app.ui].dependencies]
    project.add("dependencies", sorted(dependencies))
    document.add("project", project)
    return tomlkit.dumps(document)


def write_files(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())


def scaffold(root: Path, *, platform: PlatformConfig, app: AppConfig, version: str) -> None:
    sbu = platform.sbu(app.app.sbu)
    write_files(root, assets.starter_files(app.app.ui, title=title_for(app.app.name), sbu_name=sbu.name))
    for job in app.jobs:
        write_files(root, assets.job_files(job.name))
    (root / APP_FILE).write_text(render(app), encoding="utf-8")
    (root / "pyproject.toml").write_text(initial_pyproject(platform, app), encoding="utf-8")
    sync.write(root, app, platform, version)


def add_job_files(root: Path, job: Job) -> list[str]:
    files = assets.job_files(job.name)
    existing = [path for path in files if (root / path).exists()]
    if existing:
        return []
    write_files(root, files)
    return sorted(files)
