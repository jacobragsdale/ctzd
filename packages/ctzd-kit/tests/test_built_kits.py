"""Kits built under tmp_path: a source read for real, the errors a broken kit.toml or source.toml gives, and the kit
contract finding each problem in a kit built to break it (and none in the worked examples)."""

import importlib
import json
import os
import re
import textwrap
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from ctzd_common import samples
from ctzd_kit import DataError, SourceError, contract, testing
from ctzd_kit.sources import load_kit

type MakeKit = Callable[[str, Mapping[str, str]], str]


@pytest.fixture(autouse=True)
def desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop with nothing faked, no source's secret, and no sample rows served yet."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    samples.reset()


@pytest.fixture
def make_kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MakeKit:
    """Write a kit package `module` under tmp_path from `files`, each a path inside the package and its text, and put
    it on sys.path. Module and source names must be unique to the test: a process remembers both."""
    root = tmp_path / "kits"

    def make(module: str, files: Mapping[str, str]) -> str:
        package = root / module
        written = {"__init__.py": "", "kit.toml": 'sbu = "tk"\ntitle = "Test Kit"\nteam = "sbu-tk"\nowners = ["@contoso/sbu-tk"]\n', **files}
        for name, text in written.items():
            path = package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(text), encoding="utf-8")
        for folder in [package / "sources", *(package / "sources").glob("*/")]:
            if folder.is_dir() and not (folder / "__init__.py").exists():
                (folder / "__init__.py").write_text("", encoding="utf-8")
        monkeypatch.syspath_prepend(str(root))
        return module

    return make


LOADER = """
    import pandas as pd

    from ctzd_kit import loader


    @loader
    def {name}(as_of: "date", n: int, flag: bool = False) -> pd.DataFrame:
        {doc}
        raise NotImplementedError((as_of, n, flag))
"""


def test_a_package_source_with_no_secret_runs_its_loader(make_kit: MakeKit) -> None:
    module = make_kit(
        "ctzd_seam_run",
        {
            "sources/seam_run/source.toml": 'description = "Made-up positions"\nkind = "package"\n',
            "sources/seam_run/loaders.py": """
                import pandas as pd

                from ctzd_kit import loader


                @loader
                def positions() -> pd.DataFrame:
                    \"\"\"Positions.\"\"\"
                    return pd.DataFrame({"book_id": ["B1"], "quantity": [3]})


                @loader
                def numbered() -> pd.DataFrame:
                    \"\"\"Positions numbered as a database numbers them.\"\"\"
                    return pd.DataFrame({"book_id": [7], "quantity": [3]})
            """,
        },
    )
    found = importlib.import_module(f"{module}.sources.seam_run.loaders")
    with testing.given({"CTZD_ENV": "prod"}):
        positions = found.positions()
        with pytest.raises(DataError, match=re.escape("seam_run.numbered: book_id hold numbers")):
            found.numbered()
    assert positions.to_dict("list") == {"book_id": ["B1"], "quantity": [3.0]}
    assert samples.served() == frozenset()


def test_a_kit_with_a_bad_source_toml_names_the_file(make_kit: MakeKit) -> None:
    module = make_kit("ctzd_bad_toml", {"sources/bad_toml/source.toml": 'kind = "oracle"\n'})
    with pytest.raises(SourceError, match=r"ctzd_bad_toml/sources/bad_toml/source.toml is invalid"):
        load_kit(module)


def test_a_kit_may_not_name_a_source_sample(make_kit: MakeKit) -> None:
    module = make_kit("ctzd_bad_sample", {"sources/sample/source.toml": 'description = "x"\nkind = "sample"\n'})
    with pytest.raises(SourceError, match="not a source name"):
        load_kit(module)


def test_a_kit_without_kit_toml_says_so(make_kit: MakeKit) -> None:
    module = make_kit("ctzd_bad_kit", {"kit.toml": 'sbu = "Not A Code"\n'})
    with pytest.raises(SourceError, match=re.escape("kit.toml is missing or invalid")):
        load_kit(module)


def test_a_broken_kit_has_each_of_its_problems_named(make_kit: MakeKit) -> None:
    module = make_kit(
        "ctzd_broken",
        {
            "__init__.py": "from ctzd_kit import store\n",
            "helpers/__init__.py": "from ctzd_kit import query\n",
            "sources/brk_api/source.toml": 'description = "An API"\nkind = "rest"\ndesktop = false\n[secret.env]\nBRK_TOKEN = "token"\n',
            "sources/brk_api/loaders.py": LOADER.format(name="quotes", doc="pass"),
            "sources/brk_api/fixtures/quotes.csv": "isin,price\n",
            "sources/brk_pkg/source.toml": 'description = "A package"\nkind = "package"\n',
            "sources/brk_pkg/loaders.py": LOADER.format(name="positions", doc='"""Positions."""'),
            "sources/brk_none/source.toml": 'description = "Nothing yet"\nkind = "package"\n',
            "sources/brk_none/loaders.py": "",
            "sources/brk_bad/source.toml": 'description = "Broken"\nkind = "package"\n',
            "sources/brk_bad/loaders.py": "import fi_no_such_package\n",
        },
    )
    found = contract.check(module)
    assert found["warnings"] == ["brk_api cannot run on a desktop, so apps there always see its sample rows: a fallback to fix."]
    expected = [
        "does not give query, sample, brk_api, brk_bad, brk_none, brk_pkg",
        "brk_api is a rest source, whose secret arrives whole as CTZD_SOURCE_BRK_API",
        "brk_api.quotes has no docstring",
        "brk_api.quotes's sample rows are empty",
        "brk_bad's loaders do not import",
        "brk_none has no loader",
        "brk_pkg.positions failed on its sample rows: brk_pkg has no sample rows for positions",
        "helpers/__init__.py imports ctzd_kit",
        "NOTES.md is missing",
        "The department's skill does not render: No module named 'fi_no_such_package'",
    ]
    for part in expected:
        assert sum(part in problem for problem in found["problems"]) == 1, (part, found["problems"])
    assert len(found["problems"]) == len(expected)


def test_a_kit_whose_helpers_reach_for_the_network_fails(make_kit: MakeKit) -> None:
    module = make_kit(
        "ctzd_chatty",
        {
            "__init__.py": "from ctzd_kit import app_query as query\nfrom ctzd_kit import sample, store\n",
            "NOTES.md": "Chatty's notes.\n",
            "helpers/__init__.py": 'import socket\n\nsocket.getaddrinfo("rates.contoso.com", 443)\n',
        },
    )
    assert contract.check(module)["problems"] == [
        f"{module}.helpers does not import with the network blocked: This code reached for the network, which a kit allows only inside a source's loaders. Make the read a loader, with sample rows."
    ]


def test_a_kit_that_does_not_load_is_one_problem(make_kit: MakeKit) -> None:
    module = make_kit("ctzd_unloadable", {"kit.toml": "sbu = 1\n"})
    (problem,) = contract.check(module)["problems"]
    assert problem.startswith("The kit does not load:")


def test_main_prints_json_and_exits_by_the_problems(capsys: pytest.CaptureFixture[str], make_kit: MakeKit) -> None:
    assert contract.main(["ctzd_fi"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problems": [], "warnings": []}
    assert contract.main([make_kit("ctzd_main_broken", {})]) == 1
    assert json.loads(capsys.readouterr().out)["problems"]
    assert contract.main([]) == 2


def test_the_worked_examples_meet_the_contract() -> None:
    assert contract.check("ctzd_fi") == {"problems": [], "warnings": []}
    mas = contract.check("ctzd_mas")
    assert mas["problems"] == []
    assert mas["warnings"] == ["mas_reports cannot run on a desktop, so apps there always see its sample rows: a fallback to fix."]
