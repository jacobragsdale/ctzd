"""A `file` source: CSV, Excel or Parquet files in a folder or a blob container. Inside one of its loaders,
`read("prices/2026-11-13.csv")` gives a file's rows, every number a float64, and `listed("prices")` the files under a
folder, oldest name first."""

import io
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import pandas as pd
import pyarrow.fs as pafs

from ctzd_kit import frames
from ctzd_kit.connections import FileSource, connection
from ctzd_kit.errors import SourceError
from ctzd_kit.loaders import current

_READERS = (".csv", ".xlsx", ".parquet")


def _opened() -> tuple[pafs.FileSystem, str, str]:
    source = current()
    config = connection(source.name)
    if not isinstance(config, FileSource):
        msg = f"Source {source.name!r} is a {config.kind} source, not a folder of files."
        raise SourceError(msg)
    parts = urlsplit(config.url)
    if parts.scheme == "abfs":
        if parts.username is None or parts.hostname is None:
            msg = f"Source {source.name!r}'s url must look like abfs://container@account.dfs.core.windows.net/prefix."
            raise SourceError(msg)
        token = None if config.sas_token is None else config.sas_token.get_secret_value()
        return pafs.AzureFileSystem(account_name=parts.hostname.split(".", 1)[0], sas_token=token), (parts.username + parts.path).rstrip("/"), source.name
    return pafs.LocalFileSystem(), Path(parts.path if parts.scheme == "file" else config.url).resolve().as_posix(), source.name


def _inside(root: str, name: str) -> str:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        msg = f"{name!r} is not a path inside the source's folder."
        raise SourceError(msg)
    return f"{root}/{relative.as_posix()}"


def read(name: str) -> pd.DataFrame:
    """The rows of the file `name`, a path inside the source's folder ending in .csv, .xlsx or .parquet."""
    filesystem, root, source = _opened()
    suffix = PurePosixPath(name).suffix.lower()
    if suffix not in _READERS:
        msg = f"{name!r} is not a file ctzd reads: give a .csv, .xlsx or .parquet file."
        raise SourceError(msg)
    path = _inside(root, name)
    if filesystem.get_file_info(path).type == pafs.FileType.NotFound:
        msg = f"{source} has no file {name!r}."
        raise SourceError(msg)
    with filesystem.open_input_file(path) as stream:
        data: bytes = stream.read()
    if suffix == ".csv":
        frame = pd.read_csv(io.BytesIO(data))
    elif suffix == ".xlsx":
        frame = pd.read_excel(io.BytesIO(data))
    else:
        frame = pd.read_parquet(io.BytesIO(data))
    return frames.floats(frame, what=f"{source} {name}")


def listed(folder: str = "") -> list[str]:
    """The files under `folder` of the source, by their paths inside it, in name order."""
    filesystem, root, _source = _opened()
    base = _inside(root, folder) if folder else root
    found = filesystem.get_file_info(pafs.FileSelector(base, recursive=True, allow_not_found=True))
    return sorted(info.path.removeprefix(root).lstrip("/") for info in found if info.type == pafs.FileType.File)
