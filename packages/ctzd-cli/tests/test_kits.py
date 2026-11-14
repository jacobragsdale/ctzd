"""`ctzd kit new`, `kit add source` and the parts of `kit check` that need no network, and what `ctzd check` takes
from an app's kit."""

import tomllib
from pathlib import Path

import pytest

from ctzd_cli import checks, config, kitfacts, kitwork, sync
from ctzd_cli.errors import UsageError

from .conftest import app_config, make_app

REPO = Path(__file__).resolve().parents[3]
WORKSPACE = """[project]
name = "ws"
version = "0"

[dependency-groups]
dev = ["ctzd"]

[tool.uv]
package = false

[tool.uv.workspace]
members = ["kits/*"]

[tool.uv.sources]
ctzd = { workspace = true }
"""


def _workspace(tmp_path: Path) -> Path:
    """A uv workspace like the platform repository's, with nothing in its dev group yet."""
    (tmp_path / "pyproject.toml").write_text(WORKSPACE)
    (tmp_path / "kits").mkdir()
    return tmp_path


def test_a_new_kit_has_the_contracts_shape_and_joins_the_workspace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    kit, written = kitwork.new(workspace / "kits", sbu="eq", title="Equities")
    assert "src/ctzd_eq/kit.toml" in written
    project = tomllib.loads((kit.root / "pyproject.toml").read_text())["project"]
    assert project["name"] == "ctzd-eq"
    assert project["entry-points"]["ctzd.kits"] == {"eq": "ctzd_eq"}
    root = tomllib.loads((workspace / "pyproject.toml").read_text())
    assert "ctzd-eq" in root["dependency-groups"]["dev"]
    assert root["tool"]["uv"]["sources"]["ctzd-eq"] == {"workspace": True}
    assert kitwork.shape(kit, workspace) == []


def test_each_kind_of_source_gets_a_working_scaffold_and_the_kits_init_names_it(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    kit, _ = kitwork.new(workspace / "kits", sbu="eq", title="Equities")
    for kind in kitwork.SOURCE_KINDS:
        written = kitwork.add_source(kit, name=f"eq_{kind}", kind=kind)
        assert f"src/ctzd_eq/sources/eq_{kind}/loaders.py" in written
        assert f"tests/test_eq_{kind}.py" in written
    assert (kit.package / "sources" / "eq_oracle" / "sql" / "example.sql").is_file()
    init = (kit.package / "__init__.py").read_text()
    assert "from ctzd_eq.sources.eq_rest import loaders as eq_rest" in init
    assert '"query", "sample", "store"' in init
    problems = kitwork.shape(kit, workspace)
    assert problems
    assert all("scaffold's words" in problem for problem in problems)


@pytest.mark.parametrize(("name", "kind"), [("sample", "oracle"), ("Bad-Name", "oracle"), ("fine", "snowflake")])
def test_a_source_needs_a_name_and_a_kind_the_platform_has(tmp_path: Path, name: str, kind: str) -> None:
    kit, _ = kitwork.new(_workspace(tmp_path) / "kits", sbu="eq", title="Equities")
    with pytest.raises(UsageError):
        kitwork.add_source(kit, name=name, kind=kind)


def test_an_edited_init_is_a_shape_problem(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    kit, _ = kitwork.new(workspace / "kits", sbu="eq", title="Equities")
    (kit.package / "__init__.py").write_text("from ctzd_kit import store\n")
    assert any("__init__.py" in problem for problem in kitwork.shape(kit, workspace))


def test_the_repositorys_own_kits_have_the_contracts_shape() -> None:
    for sbu in ("fi", "mas"):
        kit = kitwork.find_kit(REPO / "kits" / sbu / "src")
        assert kitwork.shape(kit, REPO) == []


def test_an_app_on_fi_installs_the_kit_and_the_desks_own_package_from_the_workspace() -> None:
    kit = kitwork.find_kit(REPO / "kits" / "fi")
    assert kitwork.local_packages(REPO, kit) == ["ctzd-common", "ctzd-fi", "ctzd-kit", "fi-risklib"]


FACTS = kitfacts.KitFacts(
    kit="ctzd_fi",
    sbu="fi",
    title="Fixed Income",
    sources=(
        kitfacts.SourceFacts(name="fi_pricing", kind="oracle", description="prices", refreshed=None, dialect="oracle", desktop=True, io=(), credentials=False, serves_sample="none", loaders=()),
        kitfacts.SourceFacts(name="sample", kind="sample", description="rows", refreshed=None, dialect=None, desktop=True, io=(), credentials=False, serves_sample="sample", loaders=()),
    ),
    io=("fi_risklib",),
    skill="---\nname: sbu-fi\n---\n",
)


def test_the_kits_io_packages_are_forbidden_to_the_app_in_its_importlinter(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    sync.write_build(tmp_path, config.load(), app, kitfacts.tokens(FACTS))
    text = (tmp_path / ".importlinter").read_text()
    assert "    ctzd_fi\n" in text
    assert "    mssql_python\n    fi_risklib\n" in text
    assert sync.drift(tmp_path, config.load(), app, kitfacts.tokens(FACTS)) == []
    assert sync.Drift(".importlinter", "changed") in sync.drift(tmp_path, config.load(), app, {"io_modules": ""})


def test_an_apps_own_sql_is_parsed_in_its_sources_dialect(tmp_path: Path) -> None:
    data = tmp_path / "src" / "app" / "data"
    (data / "sql").mkdir(parents=True)
    (data / "__init__.py").write_text(
        'from ctzd_fi import query\n\n\ndef prices(day):\n    return query("fi_pricing", "prices.sql", as_of=day)\n\n\ndef curve(day):\n    return query("sample", "curve.sql", as_of=day)\n'
    )
    assert checks.sql_dialects(tmp_path, FACTS) == {"prices.sql": ("oracle",)}
    (data / "sql" / "prices.sql").write_text("SELECT TOP 5 isin FROM prices WHERE as_of = %(as_of)s")
    (data / "sql" / "curve.sql").write_text("SELECT TOP 5 tenor FROM curve WHERE as_of = %(as_of)s")
    step = checks.sql_files(tmp_path, checks.sql_dialects(tmp_path, FACTS))
    assert step.state == "failed"
    assert "prices.sql" in step.detail
    assert "curve.sql" not in step.detail


def test_app_toml_names_only_the_kits_sources_and_every_job_has_main(tmp_path: Path) -> None:
    app = make_app(tmp_path, app_config(kind="job", sources=("sample", "fi_trading")))
    target = checks.Target(root=tmp_path, app=app, platform=config.load())
    found = checks.app_toml(target, frozenset({"sample", "fi_pricing"}))
    assert found.state == "failed"
    assert "fi_trading" in found.detail
    assert "at least one [[jobs]]" in found.detail


def test_the_template_sample_warns_locally_and_fails_in_ci(tmp_path: Path) -> None:
    make_app(tmp_path)
    local = checks.sample(tmp_path, "local")
    assert local.state == "passed"
    assert local.warning is not None
    assert "src/app/pages/positions.py" in local.warning
    assert checks.sample(tmp_path, "ci").state == "failed"
    page = tmp_path / "src" / "app" / "pages" / "positions.py"
    page.write_text(page.read_text().partition("\n")[2])
    assert checks.sample(tmp_path, "ci") == checks.Step("sample", "passed")


def test_the_repository_name_comes_from_ci_or_the_git_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert checks.repository_name(tmp_path) is None
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/contoso-ctzd-apps/fi-bond-prices.git\n')
    assert checks.repository_name(tmp_path) == "fi-bond-prices"
    monkeypatch.setenv("GITHUB_REPOSITORY", "contoso-ctzd-apps/fi-other")
    assert checks.repository_name(tmp_path) == "fi-other"
