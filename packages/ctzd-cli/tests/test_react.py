"""A React app's platform pieces: where its pages' npm packages come from, and its locked files."""

from pathlib import Path

import pytest

from ctzd_cli import assets, config, releases, shot
from ctzd_cli.config import NpmConfig
from ctzd_cli.errors import CliError

SEED = releases.seed()


def test_a_file_source_under_home_is_expanded() -> None:
    npm = NpmConfig(packages={"@basis/ui": "file:~/dist/basis-ui.tgz", "zod": "4.6.7"})
    assert npm.spec("@basis/ui") == f"file:{Path.home() / 'dist' / 'basis-ui.tgz'}"
    assert npm.spec("zod") == "4.6.7"


def test_a_package_without_a_source_is_refused() -> None:
    with pytest.raises(CliError, match="no source for the npm package"):
        NpmConfig().spec("@basis/ui")


def test_the_bundled_platform_config_names_basis_ui_s_packages() -> None:
    assert set(config.load().npm.packages) >= {"@basis/ui", "@basis/parallax"}


def test_a_react_app_locks_its_pages_configuration_and_its_own_dockerfile() -> None:
    react = assets.locked_files(SEED, "react")
    jobs = assets.locked_files(SEED, "none")
    assert {"web/vite.config.ts", "web/tsconfig.json", "web/src/styles.css", "web/src/parallax.ts"} <= react.keys()
    assert not any(path.startswith("web/") for path in jobs)
    assert "pnpm install --frozen-lockfile" in react["Dockerfile"]
    assert "pnpm" not in jobs["Dockerfile"]


def test_the_react_starter_fills_in_the_npm_sources() -> None:
    files = assets.starter_files(SEED, "react", {"name": "fi-demo", "title": "Demo", "sbu_name": "FI", "npm:@basis/ui": "file:/u.tgz", "npm:@basis/parallax": "file:/p.tgz"})
    assert '"@basis/ui": "file:/u.tgz"' in files["web/package.json"]
    assert '"@basis/parallax": "file:/p.tgz"' in files["web/package.json"]
    assert "{{" not in files["web/package.json"]
    assert "src/app/api/datasets.py" in files


@pytest.mark.parametrize(("page", "name"), [("/", "index.png"), ("/bonds/US912810TM0", "bonds-us912810tm0.png"), ("/Sector Prices", "sector-prices.png")])
def test_each_page_s_picture_has_its_own_file(tmp_path: Path, page: str, name: str) -> None:
    assert shot.picture_path(tmp_path, page) == tmp_path / ".ctzd" / "shots" / name
