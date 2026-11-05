"""Typed wrappers around pyarrow, which ships no type information.

Everything that touches pyarrow lives here so its untyped values never reach
the rest of the package.
"""

from collections.abc import Sequence
from typing import Protocol

import pandas as pd
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq


class ArrowStream(Protocol):
    """Anything exposing the Arrow C stream interface, such as an oracledb DataFrame."""

    def __arrow_c_stream__(self, requested_schema: object = None) -> object: ...


def to_pandas(data: ArrowStream | pa.Table) -> pd.DataFrame:
    """Convert Arrow data to pandas.

    Decimal columns become float64 and date columns become datetime64, because
    app code treats numbers as floats and dates as timestamps.
    """
    table = data if isinstance(data, pa.Table) else pa.table(data)
    fields = [field.with_type(pa.float64()) if pa.types.is_decimal(field.type) else field for field in table.schema]
    converted = table.cast(pa.schema(fields))
    frame: pd.DataFrame = converted.to_pandas(date_as_object=False)
    return frame


class Storage:
    """A directory tree on the local disk or in ADLS Gen2."""

    def __init__(self, filesystem: pafs.FileSystem, root: str) -> None:
        self._fs = filesystem
        self.root = root.rstrip("/")

    @classmethod
    def local(cls, root: str) -> "Storage":
        return cls(pafs.LocalFileSystem(), root)

    @classmethod
    def azure(cls, *, account: str, container_path: str, sas_token: str | None) -> "Storage":
        filesystem = pafs.AzureFileSystem(account_name=account, sas_token=sas_token)
        return cls(filesystem, container_path)

    def path(self, *parts: str) -> str:
        return "/".join([self.root, *parts])

    def write_frame(self, path: str, frame: pd.DataFrame) -> None:
        table = pa.Table.from_pandas(frame, preserve_index=False)
        pq.write_table(table, path, filesystem=self._fs)

    def read_frame(self, path: str, columns: Sequence[str] | None) -> pd.DataFrame:
        table = pq.read_table(path, filesystem=self._fs, columns=None if columns is None else list(columns))
        frame: pd.DataFrame = table.to_pandas()
        return frame

    def write_text(self, path: str, text: str) -> None:
        with self._fs.open_output_stream(path) as stream:
            stream.write(text.encode())

    def read_text(self, path: str) -> str | None:
        info = self._fs.get_file_info(path)
        if info.type == pafs.FileType.NotFound:
            return None
        with self._fs.open_input_stream(path) as stream:
            data: bytes = stream.read()
        return data.decode()

    def make_dirs(self, path: str) -> None:
        self._fs.create_dir(path, recursive=True)

    def move(self, source: str, destination: str) -> None:
        self._fs.move(source, destination)

    def child_dirs(self, path: str) -> list[str]:
        selector = pafs.FileSelector(path, allow_not_found=True)
        return sorted(info.base_name for info in self._fs.get_file_info(selector) if info.type == pafs.FileType.Directory)

    def delete_dir(self, path: str) -> None:
        self._fs.delete_dir(path)
