"""Write a new app from this CLI's template for its kind."""

from pathlib import Path

import tomlkit

from ctzd_cli import assets, sync
from ctzd_cli.appconfig import APP_FILE, AppConfig, render
from ctzd_cli.config import PlatformConfig


def initial_pyproject(platform: PlatformConfig, app: AppConfig) -> str:
    document = tomlkit.document()
    project = tomlkit.table()
    project.add("name", platform.repo_name(app.app.sbu, app.app.name))
    project.add("version", "0.0.0")
    project.add("requires-python", assets.build().python.requires)
    dependencies = [platform.sbu(app.app.sbu).kit, *assets.build().kinds[app.app.kind].dependencies]
    project.add("dependencies", sorted(dependencies))
    document.add("project", project)
    return tomlkit.dumps(document)


def write_files(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())


def scaffold(root: Path, *, platform: PlatformConfig, app: AppConfig) -> None:
    """The template, app.toml, pyproject.toml, the build files and the guidance. The build files that come from the kit
    are written once the kit is installed (`ctzd check`)."""
    write_files(root, assets.template_files(app.app.kind, sync.tokens(platform, app)))
    (root / APP_FILE).write_text(render(app), encoding="utf-8")
    (root / "pyproject.toml").write_text(initial_pyproject(platform, app), encoding="utf-8")
    sync.write_build(root, platform, app)
    sync.write_env_example(root, app)
    sync.write_guidance(root)
