"""ctzd's own Node: reading versions, and unpacking Node's zip without its top folder."""

import io
import os
import zipfile
from pathlib import Path

import pytest

from ctzd_cli import node
from ctzd_cli.errors import CliError


def zipped(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, f"contents of {name}")
    return buffer.getvalue()


@pytest.mark.parametrize(("version", "major"), [("v26.13.0", 26), ("12.18.0\n", 12), ("not a version", None)])
def test_the_major_version_is_read(version: str, major: int | None) -> None:
    assert node.major(version) == major


def test_node_s_zip_is_unpacked_without_its_top_folder(tmp_path: Path) -> None:
    node.unpack_node(zipped("node-v26.13.0-win-x64/node.exe", "node-v26.13.0-win-x64/node_modules/npm/bin/npm-cli.js"), tmp_path / "node")
    assert (tmp_path / "node" / "node.exe").read_text() == "contents of node-v26.13.0-win-x64/node.exe"
    assert (tmp_path / "node" / "node_modules" / "npm" / "bin" / "npm-cli.js").is_file()


def test_a_zip_that_climbs_out_of_its_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="unsafe path"):
        node.unpack_node(zipped("node/../../evil.txt"), tmp_path / "node")


def test_ctzd_s_own_folders_come_first_on_the_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTZD_HOME", str(tmp_path))
    first = node.search_path().split(os.pathsep)[0]
    assert first.startswith(str(tmp_path))
