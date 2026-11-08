"""The `numbers` gate: an app's Python and SQL never round or format a number."""

from pathlib import Path

import pytest

from ctzd_cli import checks, rounding


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "line",
    [
        "x = round(price, 2)",
        "x = frame.round(2)",
        "x = frame['price'].round()",
        "x = np.around(values, 2)",
        "x = numpy.rint(values)",
        "import decimal",
        "from decimal import Decimal",
        "x = f'{price:.2f}'",
        "x = f'{total:,}'",
        "x = f'{share:%}'",
        "x = f'{price:>10.3f}'",
        "x = format(price, '.2f')",
        "x = '{:,.0f} bonds'.format(count)",
        "x = '%.2f' % price",
        "x = 'about %d bonds' % count",
        "x = frame.to_csv(float_format='%.2f')",
    ],
)
def test_python_that_rounds_or_formats_a_number_is_refused(tmp_path: Path, line: str) -> None:
    write(tmp_path, "src/app/logic/summary.py", f"{line}\n")
    found = rounding.problems(tmp_path)
    assert len(found) == 1
    assert found[0].startswith("src/app/logic/summary.py:1: ")


@pytest.mark.parametrize(
    "line",
    [
        "x = f'{name}: {price}'",
        "x = f'{name:>10}'",
        "x = '{} bonds'.format(count)",
        "x = 'report for %s' % day",
        "x = math.floor(value / 1000)",
        "x = frame['price'].mean()",
        "x = ctzd.display.number(price, decimals=2)",
    ],
)
def test_python_that_leaves_numbers_alone_passes(tmp_path: Path, line: str) -> None:
    write(tmp_path, "src/app/jobs/daily.py", f"{line}\n")
    assert rounding.problems(tmp_path) == []


def test_tests_are_not_checked(tmp_path: Path) -> None:
    write(tmp_path, "tests/test_logic.py", "assert round(price, 2) == 1.5\n")
    assert rounding.problems(tmp_path) == []


@pytest.mark.parametrize(
    ("sql", "names"),
    [
        ("SELECT ROUND(price, 2) AS price FROM prices", "ROUND"),
        ("SELECT CAST(price AS NUMBER(10, 2)) AS price FROM prices", "NUMBER(10, 2)"),
        ("SELECT CAST(par AS NUMBER(12)) AS par FROM positions", "NUMBER(12)"),
        ("SELECT TO_CHAR(price, 'FM990.00') AS price FROM prices", "TO_CHAR"),
        ("SELECT CONVERT(DECIMAL(10, 2), price) AS price FROM prices", "NUMERIC(10, 2)"),
        ("SELECT TRY_CAST(price AS NUMERIC(5, 1)) AS price FROM prices", "NUMERIC(5, 1)"),
        ("SELECT CAST(price AS MONEY) AS price FROM prices", "MONEY"),
        ("SELECT FORMAT(price, 'N2') AS price FROM prices", "FORMAT"),
        ("SELECT STR(price, 10, 2) AS price FROM prices", "STR"),
    ],
)
def test_sql_that_rounds_or_formats_a_number_is_refused(tmp_path: Path, sql: str, names: str) -> None:
    write(tmp_path, "src/app/data/sql/prices.sql", sql)
    found = rounding.problems(tmp_path)
    assert len(found) == 1
    assert found[0].startswith("src/app/data/sql/prices.sql: ")
    assert names in found[0]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT TO_CHAR(security_id) AS security_id, TO_CHAR(as_of, 'YYYY-MM-DD') AS day FROM prices WHERE as_of = :as_of",
        "SELECT CAST(security_id AS VARCHAR(40)) AS security_id, CAST(price AS FLOAT) AS price FROM prices WHERE as_of = %(as_of)s",
        "SELECT CAST(price AS NUMBER) AS price, CAST(x AS BINARY_DOUBLE) AS x, TRUNC(as_of) AS day FROM prices",
        "SELECT FORMAT(as_of, 'yyyy-MM-dd') AS day, FLOOR(par / 1000) AS bucket FROM positions",
    ],
)
def test_sql_that_leaves_numbers_alone_passes(tmp_path: Path, sql: str) -> None:
    write(tmp_path, "src/app/data/sql/prices.sql", sql)
    assert rounding.problems(tmp_path) == []


def test_the_step_lists_each_problem_and_says_how_to_fix_it(tmp_path: Path) -> None:
    write(tmp_path, "src/app/logic/summary.py", "x = round(price, 2)\ny = frame.round(1)\n")
    step = checks.numbers(tmp_path)
    assert step.state == "failed"
    assert step.detail.splitlines() == ["src/app/logic/summary.py:1: round() rounds a number.", "src/app/logic/summary.py:2: .round() rounds numbers.", rounding.FIX]


def test_the_step_passes_an_app_that_rounds_nothing(tmp_path: Path) -> None:
    write(tmp_path, "src/app/logic/summary.py", "def share(part: float, whole: float) -> float:\n    return part / whole\n")
    assert checks.numbers(tmp_path).state == "passed"
