"""The kit contract's checks that need the kit imported, run by `ctzd kit check` in the kit's environment:

    python -m ctzd_kit.contract MODULE      prints {"problems": [...], "warnings": [...]}

With the network blocked throughout:

- **sources**: every source's `source.toml` reads, says what the source holds, and takes its secret the way its kind
  does: a driver's as `CTZD_SOURCE_<NAME>`, so it declares no `[secret]`;
- **loaders and fakes**: every source has a loader, every loader a docstring and sample rows, and each one, called on
  its sample rows with an example of each argument, answers a frame whose numbers are float64 and whose ids are text;
- **network only inside loaders**: the kit's helpers import, and touch neither the network, nor `ctzd_kit`, nor a
  source's packages;
- **what apps import**: the kit's package gives `query`, `store`, `sample` and each source by its name;
- **guidance**: `NOTES.md` is there, and the department's skill renders.

It warns about each source that cannot run on a desktop: a fallback to fix.
"""

import ast
import importlib
import inspect
import json
import sys
from datetime import UTC, date, datetime
from importlib import resources
from importlib.resources.abc import Traversable

from ctzd_kit import nonetwork, testing
from ctzd_kit.errors import KitError
from ctzd_kit.explore import describe_source, loaders_of
from ctzd_kit.loaders import Loader
from ctzd_kit.skill import render
from ctzd_kit.sources import DRIVERS, Kit, Source, load_kit

EXPORTS = ("query", "store", "sample")
_EXAMPLES: dict[object, object] = {date: date(2026, 11, 13), "date": date(2026, 11, 13), datetime: datetime(2026, 11, 13, 7, tzinfo=UTC), "datetime": datetime(2026, 11, 13, 7, tzinfo=UTC)}
_EXAMPLES |= {int: 1, "int": 1, float: 1.0, "float": 1.0, bool: True, "bool": True, str: "x", "str": "x"}


def _example(parameter: inspect.Parameter) -> object:
    return _EXAMPLES.get(parameter.annotation, "x")


def _loader_problems(source: Source, found: Loader) -> list[str]:
    where = f"{source.name}.{found.name}"
    problems = [] if found.doc.strip() else [f"{where} has no docstring: say what it returns, in the department's words, since the skill and `ctzd sources` show it."]
    arguments = {name: _example(parameter) for name, parameter in found.signature.parameters.items() if parameter.default is inspect.Parameter.empty}
    try:
        frame = found.function(**arguments)
    except (KitError, nonetwork.NetworkBlockedError) as err:
        return [*problems, f"{where} failed on its sample rows: {err}"]
    if frame.empty:
        problems.append(f"{where}'s sample rows are empty: give it a few made-up rows, one column per column the real read returns.")
    return problems


def _source_problems(source: Source) -> list[str]:
    problems: list[str] = []
    if source.spec.kind in DRIVERS and source.spec.secret is not None:
        problems.append(f"{source.name} is a {source.spec.kind} source, whose secret arrives whole as CTZD_SOURCE_{source.name.upper()}: [secret] is for package sources.")
    try:
        found = loaders_of(source)
    except (ImportError, KitError, nonetwork.NetworkBlockedError) as err:
        return [*problems, f"{source.name}'s loaders do not import: {err}"]
    if not found:
        problems.append(f"{source.name} has no loader: every read an app can make is a @loader in sources/{source.name}/loaders.py.")
    for loader in found:
        problems += _loader_problems(source, loader)
    return problems


def _imports(folder: Traversable) -> list[tuple[str, str]]:
    """Each module the Python files under `folder` import, with the file's name."""
    found: list[tuple[str, str]] = []
    for entry in folder.iterdir() if folder.is_dir() else ():
        if entry.is_dir():
            found += _imports(entry)
            continue
        if not entry.name.endswith(".py"):
            continue
        tree = ast.parse(entry.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [(entry.name, alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
                found.append((entry.name, node.module))
    return found


def _helper_problems(kit: Kit) -> list[str]:
    folder = kit.folder / "helpers"
    if not folder.is_dir():
        return []
    banned = {"ctzd_kit", f"{kit.module}.sources", *(package for source in kit.sources.values() for package in source.spec.io)}
    problems = [
        f"helpers/{name} imports {module}: helpers are pure, so apps may call them in logic/; a read belongs in a source's loader."
        for name, module in _imports(folder)
        if any(module == ban or module.startswith(f"{ban}.") for ban in banned)
    ]
    try:
        importlib.import_module(f"{kit.module}.helpers")
    except (ImportError, nonetwork.NetworkBlockedError) as err:
        problems.append(f"{kit.module}.helpers does not import with the network blocked: {err}")
    return problems


def check(module: str) -> dict[str, list[str]]:
    problems: list[str] = []
    warnings: list[str] = []
    with nonetwork.blocked(), testing.fakes():
        try:
            kit = load_kit(module)
            package = importlib.import_module(module)
        except (ImportError, KitError) as err:
            return {"problems": [f"The kit does not load: {err}"], "warnings": []}
        missing = [name for name in dict.fromkeys((*EXPORTS, *kit.sources)) if not hasattr(package, name)]
        if missing:
            problems.append(f"{module}/__init__.py does not give {', '.join(missing)}: apps import `query`, `store`, `sample` and each source by its name from the kit.")
        for source in kit.sources.values():
            if source.name != "sample":
                problems += _source_problems(source)
            if not source.spec.desktop:
                warnings.append(f"{source.name} cannot run on a desktop, so apps there always see its sample rows: a fallback to fix.")
        problems += _helper_problems(kit)
        if not (resources.files(module) / "NOTES.md").is_file():
            problems.append(f"{module}/NOTES.md is missing: the department's conventions, in its own words, which the skill carries.")
        try:
            render(kit, [describe_source(source) for source in kit.sources.values()])
        except (ImportError, KitError, nonetwork.NetworkBlockedError) as err:
            problems.append(f"The department's skill does not render: {err}")
    return {"problems": problems, "warnings": warnings}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write(__doc__ or "")
        return 2
    found = check(argv[0])
    sys.stdout.write(json.dumps(found) + "\n")
    return 1 if found["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
