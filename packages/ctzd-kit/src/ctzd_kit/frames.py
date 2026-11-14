"""Typed wrappers around pyarrow, which ships no type information.

Everything that touches pyarrow lives here so its untyped values never reach
the rest of the package.

Every number an app sees is a float64, NaN where it is missing: one simple type
from the database to the browser, whose numbers are float64 too. Python never
rounds a number; the page and `ctzd.display` round once, when they show it. So:

- Decimals become the float64 nearest their value, through their text. pyarrow's
  direct cast is not correctly rounded: it turns 2.675 into 2.6750000000000003,
  which a filter on `== 2.675` then misses.
- Integers become float64, which holds every whole number up to 2**53 exactly. A
  whole-number column holding a larger value is refused, because it would come out
  wrong: such a column is almost always an id, and ids are selected as text.
- float32 values become the float64 of their shortest text, so 0.1 stays 0.1
  rather than 0.10000000149011612.
"""

from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as pafs
import pyarrow.parquet as pq
from pandas.api import types as ptypes

from ctzd_kit.errors import DataError

EXACT_LIMIT = 2**53
"""The largest whole number below which every whole number is a float64 exactly."""


class ArrowStream(Protocol):
    """Anything exposing the Arrow C stream interface, such as an oracledb DataFrame."""

    def __arrow_c_stream__(self, requested_schema: object = None) -> object: ...


type Values = pa.Array[Any] | pa.ChunkedArray[Any]
"""A column of Arrow values, in one piece or in chunks."""


def _check_whole(values: Values, column: str, what: str) -> None:
    """Refuse a whole-number column holding a value a float64 cannot hold exactly."""
    extremes = pc.min_max(values)
    low, high = extremes["min"].as_py(), extremes["max"].as_py()
    if (low is not None and low < -EXACT_LIMIT) or (high is not None and high > EXACT_LIMIT):
        msg = (
            f"{what}: column {column!r} holds whole numbers beyond 2**53 (9,007,199,254,740,992), which a float64 "
            "cannot hold exactly. If it names something, such as an id or an account number, select it as text "
            f"(Oracle: TO_CHAR({column}); SQL Server: CAST({column} AS VARCHAR(40))). If it is an amount, convert it "
            f"in the query (Oracle: CAST({column} AS BINARY_DOUBLE); SQL Server: CAST({column} AS FLOAT))."
        )
        raise DataError(msg)


def _as_float(values: Values, column: str, what: str) -> Values | None:
    """`values` as a float64 Arrow array or chunked array, by the rules above; None when it holds no numbers to convert."""
    kind = values.type
    if pa.types.is_decimal(kind):
        if kind.scale <= 0:
            _check_whole(values, column, what)
        return pc.cast(pc.cast(values, pa.string()), pa.float64())
    if pa.types.is_integer(kind):
        _check_whole(values, column, what)
        return pc.cast(values, pa.float64())
    if pa.types.is_float16(kind) or pa.types.is_float32(kind):
        return pc.cast(pc.cast(values, pa.string()), pa.float64())
    return None


def _floats(table: pa.Table, what: str) -> pa.Table:
    for index, field in enumerate(table.schema):
        converted = _as_float(table.column(index), field.name, what)
        if converted is not None:
            table = table.set_column(index, field.with_type(pa.float64()), converted)
    return table


def to_pandas(data: ArrowStream | pa.Table, *, what: str = "query") -> pd.DataFrame:
    """Convert Arrow data to pandas, every number a float64 and every date a datetime64.

    `what` names the data in an error, such as the source and SQL file.
    """
    table = data if isinstance(data, pa.Table) else pa.table(data)
    frame: pd.DataFrame = _floats(table, what).to_pandas(date_as_object=False)
    return frame


def _needs_float(series: pd.Series) -> bool:
    if isinstance(series.dtype, pd.ArrowDtype) and pa.types.is_decimal(series.dtype.pyarrow_dtype):
        return True
    if ptypes.is_bool_dtype(series.dtype):
        return False
    if ptypes.is_integer_dtype(series.dtype):
        return True
    return ptypes.is_float_dtype(series.dtype) and series.dtype != "float64"


def floats(frame: pd.DataFrame, *, what: str) -> pd.DataFrame:
    """`frame` with every number a numpy float64, by the rules above; `frame` itself when it already is.

    For frames that do not come from a database: fakes in tests, REST responses,
    the store, and what app code writes to it.
    """
    changed: dict[int, np.ndarray] = {}
    for position in range(frame.shape[1]):
        series = frame.iloc[:, position]
        if not _needs_float(series):
            continue
        column = str(frame.columns[position])
        values = pa.array(series, from_pandas=True)
        converted = _as_float(values, column, what)
        floated = values if converted is None else converted
        changed[position] = floated.to_numpy(zero_copy_only=False)
    if not changed:
        return frame
    result = frame.copy()
    for position, values in changed.items():
        result.isetitem(position, values)
    return result


class Storage:
    """A directory tree on the local disk or in ADLS Gen2."""

    def __init__(self, filesystem: pafs.FileSystem, root: str) -> None:
        self._fs = filesystem
        self.root = root.rstrip("/")

    @classmethod
    def local(cls, root: str) -> Storage:
        return cls(pafs.LocalFileSystem(), root)

    @classmethod
    def azure(cls, *, account: str, container_path: str, sas_token: str | None) -> Storage:
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
