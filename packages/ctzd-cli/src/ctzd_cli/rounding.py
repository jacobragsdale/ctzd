"""The `numbers` gate: an app's Python and SQL never round or format a number.

Every number is a float64 from the source to the page. The page rounds each one
once, when it shows it, by the rule the firm's spreadsheets use, and
`ctzd.display` does the same for text a job writes into an email or a message.
Rounding anywhere earlier changes the data, and two places that round differently
show two numbers for one value. So the gate refuses, in `src/app/` outside the
page code in `src/app/ui/`:

- in Python: `round()`, the `.round()`, `.around()` and `.rint()` of pandas and
  numpy, the `decimal` module, number formats in f-strings, `format()`,
  `str.format()` and `%`, and `float_format=`;
- in SQL: `ROUND`, casts and conversions to a decimal type with a precision or to
  money, `TO_CHAR` with a number format, `FORMAT` of a number, and `STR`.

Tests are not checked: they may compare numbers however they need to.
"""

import ast
import re
import string
from collections.abc import Iterator
from pathlib import Path

import sqlglot
import sqlglot.errors
from sqlglot import exp

FIX = "Keep the number as it is: the page shows it to the decimals its column declares, and an email or Teams message writes it with ctzd.display, such as ctzd.display.number(value, decimals=2)."
_SPEC = re.compile(r"(?:.?[<>=^])?[-+ ]?z?#?0?\d*(?P<grouping>[,_])?(?:\.(?P<precision>\d+))?(?P<kind>[bcdeEfFgGnosxX%])?")
_NUMBER_KINDS = frozenset("eEfFgGn%")
_PERCENT_FORMAT = re.compile(r"%(?:\([^)]*\))?[-+ #0]*(?:\d+|\*)?(?:\.\d+)?[diueEfFgG]")
_ROUNDING_METHODS = frozenset({"round", "around", "rint"})
_PYFORMAT = re.compile(r"%\((\w+)\)s")
_ORACLE_BIND = re.compile(r"(?<![:\w]):[A-Za-z_]\w*")
_TSQL_ONLY = re.compile(r"\b(?:TRY_CAST|TRY_CONVERT|CONVERT|ISNULL|GETDATE|TOP)\b|@\w|\[\w", re.IGNORECASE)
_DECIMAL_TYPES = frozenset(
    member for name in ("DECIMAL", "BIGDECIMAL", "DECIMAL32", "DECIMAL64", "DECIMAL128", "DECIMAL256", "UDECIMAL") if (member := getattr(exp.DataType.Type, name, None)) is not None
)
_MONEY_TYPES = frozenset(member for name in ("MONEY", "SMALLMONEY") if (member := getattr(exp.DataType.Type, name, None)) is not None)
_DATE_PARTS = re.compile(r"Y|H|MM|DD|SS|MON|DAY", re.IGNORECASE)


def _numeric_spec(spec: str) -> bool:
    """Whether a format spec rounds or groups a number, such as `.2f`, `,` or `%`."""
    found = _SPEC.fullmatch(spec)
    if found is None:
        return False
    return found["grouping"] is not None or found["precision"] is not None or (found["kind"] or "") in _NUMBER_KINDS


def _constant_text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str))
    return None


def _template_rounds(template: str) -> bool:
    """Whether a `str.format` template formats a number, as `"{:.2f}"` does."""
    try:
        return any(spec is not None and _numeric_spec(spec) for _, _, spec, _ in string.Formatter().parse(template))
    except ValueError:
        return False


def _call_problem(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "round":
        return "round() rounds a number"
    if isinstance(func, ast.Attribute) and func.attr in _ROUNDING_METHODS:
        return f".{func.attr}() rounds numbers"
    if isinstance(func, ast.Name) and func.id == "format" and len(node.args) > 1:
        spec = _constant_text(node.args[1])
        if spec is not None and _numeric_spec(spec):
            return f"format(..., {spec!r}) rounds a number into text"
    if isinstance(func, ast.Attribute) and func.attr == "format":
        template = _constant_text(func.value)
        if template is not None and _template_rounds(template):
            return "str.format() with a number format rounds a number into text"
    if any(keyword.arg == "float_format" for keyword in node.keywords):
        return "float_format= rounds numbers into text"
    return None


def _node_problem(node: ast.AST) -> str | None:
    match node:
        case ast.Call():
            return _call_problem(node)
        case ast.Import(names=names) if any(alias.name == "decimal" or alias.name.startswith("decimal.") for alias in names):
            return "the decimal module is for rounding exactly, and every number here is a float"
        case ast.ImportFrom(module="decimal"):
            return "the decimal module is for rounding exactly, and every number here is a float"
        case ast.FormattedValue(format_spec=spec) if spec is not None:
            text = _constant_text(spec)
            if text is not None and _numeric_spec(text):
                return f"the f-string format {text!r} rounds a number into text"
        case ast.BinOp(left=left, op=ast.Mod()):
            text = _constant_text(left)
            if text is not None and _PERCENT_FORMAT.search(text):
                return "a % number format rounds a number into text"
        case _:
            pass
    return None


def python_problems(path: Path, root: Path) -> Iterator[str]:
    """Each place `path` rounds or formats a number, as `file:line: what`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return  # the lint step reports it
    shown = path.relative_to(root).as_posix()
    for node in ast.walk(tree):
        problem = _node_problem(node)
        if problem is not None:
            yield f"{shown}:{getattr(node, 'lineno', 0)}: {problem}."


def _parses(text: str) -> list[tuple[str, list[exp.Expr]]]:
    """The file's statements in each dialect it may be written for, and that parses it.

    Bind parameters tell: `:name` is Oracle's and `%(name)s` SQL Server's. A file
    with neither is read both ways, since each dialect knows functions the other
    does not, such as SQL Server's FORMAT and STR.
    """
    tsql = _PYFORMAT.sub(r"@\1", text)
    candidates = [("tsql", tsql)] if _PYFORMAT.search(text) else [("oracle", text)] if _ORACLE_BIND.search(text) else [("oracle", text), ("tsql", tsql)]
    parsed: list[tuple[str, list[exp.Expr]]] = []
    for dialect, source in candidates:
        try:
            parsed.append((dialect, [statement for statement in sqlglot.parse(source, read=dialect) if statement is not None]))
        except sqlglot.errors.ParseError:
            continue
    return parsed


def _cast_problem(node: exp.Expr, dialect: str) -> str | None:
    # CAST(x AS type) holds the type in `to`; SQL Server's CONVERT(type, x) in `this`.
    target = node.this if isinstance(node, exp.Convert) else node.args.get("to")
    if not isinstance(target, exp.DataType):
        return None
    kind = target.this
    if kind in _MONEY_TYPES:
        return f"{target.sql(dialect=dialect)} rounds a number to four decimals"
    if kind in _DECIMAL_TYPES and target.expressions:
        return f"{target.sql(dialect=dialect)} rounds a number to a fixed number of decimals"
    return None


def _sql_problem(node: exp.Expr, dialect: str) -> str | None:
    match node:
        case exp.Round():
            return "ROUND rounds a number"
        case exp.Cast() | exp.TryCast() | exp.Convert():
            return _cast_problem(node, dialect)
        case exp.ToChar():
            fmt = node.args.get("format")
            text = fmt.this if isinstance(fmt, exp.Literal) and fmt.is_string else None
            if isinstance(text, str) and re.search(r"[09]", text) and not _DATE_PARTS.search(text):
                return f"TO_CHAR with the number format {text!r} rounds a number into text"
        case exp.NumberToStr():
            return "FORMAT of a number rounds it into text"
        case exp.Anonymous(this=name) if isinstance(name, str) and name.upper() == "STR":
            return "STR rounds a number into text"
        case _:
            pass
    return None


def _found(dialect: str, statements: list[exp.Expr], shown: str) -> list[str]:
    found: list[str] = []
    for statement in statements:
        for node in statement.walk():
            problem = _sql_problem(node, dialect)
            if problem is not None:
                found.append(f"{shown}: {problem}: {node.sql(dialect=dialect)}.")
    return found


def sql_problems(path: Path, root: Path) -> list[str]:
    """Each place the SQL file `path` rounds or formats a number, as `file: what: the SQL`.

    A file both dialects parse is judged in the one that finds more, and on a tie in
    SQL Server's when it uses something only SQL Server has, so each problem is
    reported once and written as the file writes it.
    """
    text = path.read_text(encoding="utf-8")
    shown = path.relative_to(root).as_posix()
    readings = [(dialect, _found(dialect, statements, shown)) for dialect, statements in _parses(text)]
    if not readings:
        return []  # the sql step reports a file that does not parse
    prefer = "tsql" if _TSQL_ONLY.search(text) else "oracle"
    return max(readings, key=lambda reading: (len(reading[1]), reading[0] == prefer))[1]


def problems(root: Path) -> list[str]:
    """Every place the app's Python outside `src/app/ui/`, and its SQL, round or format a number."""
    app = root / "src" / "app"
    found: list[str] = []
    for path in sorted(app.glob("**/*.py")):
        if (app / "ui") not in path.parents:
            found.extend(python_problems(path, root))
    for path in sorted((app / "data" / "sql").glob("**/*.sql")):
        found.extend(sql_problems(path, root))
    return found
