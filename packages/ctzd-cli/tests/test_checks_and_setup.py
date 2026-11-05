from pathlib import Path

import pytest

from ctzd_cli import checks, setup_checks


@pytest.mark.parametrize(
    "sql", ["select isin, price from prices where as_of = :as_of", "SELECT TOP 10 isin FROM dbo.prices WHERE as_of = %(as_of)s", "with x as (select 1 as n from dual) select n from x"]
)
def test_oracle_and_sql_server_queries_parse(sql: str) -> None:
    assert checks.sql_problem(sql) is None


@pytest.mark.parametrize("sql", ["select from where", "-- only a comment\n"])
def test_broken_or_empty_sql_is_reported(sql: str) -> None:
    assert checks.sql_problem(sql) is not None


@pytest.mark.parametrize(("output", "version"), [("2.1.300 (Claude Code)", (2, 1, 300)), ("claude 2.0.1", (2, 0, 1)), ("unknown", None)])
def test_claude_version_is_read_from_its_output(output: str, version: tuple[int, int, int] | None) -> None:
    assert setup_checks.claude_version(output) == version


def test_a_claude_md_above_the_folder_blocks_agents_md(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# mine\n")
    work = tmp_path / "apps" / "bond-prices"
    work.mkdir(parents=True)
    assert tmp_path / "CLAUDE.md" in setup_checks.blocking_claude_files(work)
    assert setup_checks.check_claude_files(work).state == "warning"


def test_no_claude_md_means_nothing_blocks(tmp_path: Path) -> None:
    work = tmp_path / "apps"
    work.mkdir()
    assert [path for path in setup_checks.blocking_claude_files(work) if path.is_relative_to(tmp_path)] == []
