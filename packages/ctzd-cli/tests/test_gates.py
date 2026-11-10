"""Gates the plan names: the starter's sample page never ships, datasets read only through loaders, every page is smoked."""

from pathlib import Path

from ctzd_cli import checks, smoke

from .conftest import make_app


def test_the_starters_sample_page_warns_locally_and_fails_in_jenkins(tmp_path: Path) -> None:
    make_app(tmp_path)
    local = checks.sample_pages(tmp_path, "local")
    assert local.state == "passed"
    assert local.warning is not None
    assert "web/src/pages/home.tsx" in local.warning
    assert checks.sample_pages(tmp_path, "ci").state == "failed"
    page = tmp_path / "web" / "src" / "pages" / "home.tsx"
    page.write_text("\n".join(line for line in page.read_text().splitlines() if checks.SAMPLE_MARKER not in line) + "\n")
    assert checks.sample_pages(tmp_path, "ci") == checks.Step("sample pages", "passed")


def test_a_dataset_or_job_that_reads_a_source_itself_is_refused(tmp_path: Path) -> None:
    (tmp_path / "src" / "app" / "api").mkdir(parents=True)
    (tmp_path / "src" / "app" / "jobs").mkdir(parents=True)
    (tmp_path / "src" / "app" / "api" / "datasets.py").write_text('import ctzd\n\n\ndef prices():\n    return ctzd.query("fi_pricing", "sql/prices.sql")\n')
    (tmp_path / "src" / "app" / "jobs" / "daily.py").write_text('import ctzd\n\n\ndef main():\n    ctzd.write_table(ctzd.read_table("x"), "y")\n')
    found = checks.entry_point_reads(tmp_path)
    assert [line.split(": ", 1)[0] for line in found] == ["src/app/api/datasets.py:5", "src/app/jobs/daily.py:5"]
    assert all("call a loader" in line for line in found)


def test_the_smoke_test_opens_every_page_the_app_routes(tmp_path: Path) -> None:
    make_app(tmp_path)
    (tmp_path / "web" / "src" / "nav.ts").write_text('const pages = [{ path: "/", title: "Home" }, { path: "/sector-prices", title: "Sector prices" }] as const;\n')
    (tmp_path / "web" / "src" / "routes.ts").write_text('createRoute({ path: "/bonds/$isin" });\ncreateRoute({ path: "/about" });\n')
    assert smoke.page_paths(tmp_path) == ["/", "/about", "/sector-prices"]


def test_a_comment_that_turns_a_page_check_off_is_refused_except_the_clients_barrel(tmp_path: Path) -> None:
    make_app(tmp_path)
    assert checks.web_suppressions(tmp_path) == []  # the starter's src/api/client.ts disables only oxc/no-barrel-file
    page = tmp_path / "web" / "src" / "pages" / "prices.tsx"
    page.write_text("/* oxlint-disable */\nconst x = 1; // @ts-expect-error -- later\n// eslint-disable-next-line oxc/no-barrel-file\n")
    (tmp_path / "web" / "src" / "api" / "generated").mkdir(parents=True)
    (tmp_path / "web" / "src" / "api" / "generated" / "client.ts").write_text("// @ts-nocheck\n")
    found = checks.web_suppressions(tmp_path)
    assert [line.split(": ", 1)[0] for line in found] == ["web/src/pages/prices.tsx:1", "web/src/pages/prices.tsx:2", "web/src/pages/prices.tsx:3"]
