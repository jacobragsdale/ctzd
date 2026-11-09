"""A React app's platform pieces: where its pages' npm packages come from, and its locked files."""

import json
from pathlib import Path

import pytest

from ctzd_cli import api_models as m
from ctzd_cli import assets, catalogue, config, releases, shot, sync
from ctzd_cli.errors import CliError, UsageError

SEED = releases.seed()


def test_a_file_source_under_home_is_expanded() -> None:
    assert m.expand_spec("file:~/dist/basis-ui.tgz") == f"file:{Path.home() / 'dist' / 'basis-ui.tgz'}"
    assert m.expand_spec("4.6.7") == "4.6.7"


def test_the_bundled_release_pins_basis_ui_s_packages_and_the_platform_config_does_not() -> None:
    assert set(SEED.npm.packages) >= {"@basis/ui", "@basis/parallax"}
    assert config.load().release_overrides is None


def _web(root: Path, ui: str) -> None:
    (root / "web").mkdir(parents=True)
    document = {"name": "x", "dependencies": {"@basis/parallax": "1.0.0", "@basis/ui": ui, "react": "19.3.1"}}
    (root / "web" / "package.json").write_text(json.dumps(document, indent=2) + "\n")
    (root / "web" / "pnpm-workspace.yaml").write_text(f'overrides:\n  "@basis/ui": "{ui}"\n  typescript: 7.0.2\n')


def test_a_release_moves_the_pages_basis_ui_pins_and_leaves_the_rest(tmp_path: Path) -> None:
    platform = config.load().model_copy(update={"release_overrides": config.ReleaseOverrides(npm_packages={"@basis/ui": "2.0.0", "@basis/parallax": "2.0.0"})})
    _web(tmp_path, "1.0.0")
    assert [problem.path for problem in sync.web_drift(tmp_path, platform, SEED)] == [
        "web/package.json [dependencies.@basis/parallax]",
        "web/package.json [dependencies.@basis/ui]",
        "web/pnpm-workspace.yaml [overrides.@basis/ui]",
    ]
    assert sync.write_web(tmp_path, platform, SEED) == ["web/package.json", "web/pnpm-workspace.yaml"]
    assert sync.web_drift(tmp_path, platform, SEED) == []
    written = json.loads((tmp_path / "web" / "package.json").read_text())
    assert written["dependencies"] == {"@basis/parallax": "2.0.0", "@basis/ui": "2.0.0", "react": "19.3.1"}
    assert (tmp_path / "web" / "pnpm-workspace.yaml").read_text() == 'overrides:\n  "@basis/ui": "2.0.0"\n  typescript: 7.0.2\n'


def test_a_react_app_locks_its_pages_configuration_and_its_own_dockerfile() -> None:
    react = assets.locked_files(SEED, "react")
    jobs = assets.locked_files(SEED, "none")
    assert {"web/vite.config.ts", "web/tsconfig.json", "web/src/styles.css"} <= react.keys()
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


CATALOGUE = {
    "format": "basis-ui-catalogue/1",
    "package": "@basis/ui",
    "icons": ["ArrowLeftIcon"],
    "components": [
        {"name": "Card", "family": "card", "purpose": "A card. A raised panel.", "props": [], "variants": {}, "example": "<Card />"},
        {"name": "CardTitle", "family": "card", "purpose": "A card's title.", "props": [{"name": "className", "type": "string", "optional": True, "description": ""}], "variants": {}},
        {"name": "Button", "family": "button", "purpose": "A button.", "props": [], "variants": {"size": {"default": "default", "options": ["default", "sm"]}}, "extends": ["X"]},
    ],
}


def test_the_catalogue_lists_families_and_gives_a_component_its_family_s_example(tmp_path: Path) -> None:
    path = tmp_path / catalogue.CATALOGUE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(CATALOGUE), encoding="utf-8")
    found = catalogue.load(tmp_path)
    assert catalogue.overview(found)["card"] == {"purpose": "A card.", "components": ["Card", "CardTitle"]}
    title = catalogue.entry(found, "cardtitle")
    assert title["example"] == "<Card />"
    assert title["family_components"] == ["Card", "CardTitle"]
    with pytest.raises(UsageError, match="no component named 'Grid'"):
        catalogue.entry(found, "Grid")


def test_without_the_page_s_packages_there_is_no_catalogue(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="no component catalogue"):
        catalogue.load(tmp_path)
