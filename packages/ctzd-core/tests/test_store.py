import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

import ctzd
from ctzd.store import storage_for


def frame(*prices: float) -> pd.DataFrame:
    return pd.DataFrame({"isin": [f"X{i}" for i in range(len(prices))], "price": list(prices)})


def at(minute: int) -> dt.datetime:
    return dt.datetime(2026, 11, 4, 7, minute, tzinfo=dt.UTC)


def test_read_returns_the_latest_published_version(tmp_path: Path) -> None:
    with ctzd.testing.fake_store(tmp_path):
        ctzd.write_table(frame(1.0), "prices", now=lambda: at(0))
        ctzd.write_table(frame(2.0, 3.0), "prices", now=lambda: at(1))
        assert ctzd.read_table("prices")["price"].tolist() == [2.0, 3.0]


def test_columns_limits_what_is_read(tmp_path: Path) -> None:
    with ctzd.testing.fake_store(tmp_path):
        ctzd.write_table(frame(1.0), "prices", now=lambda: at(0))
        assert ctzd.read_table("prices", columns=["price"]).columns.tolist() == ["price"]


def test_empty_frame_is_refused_unless_allowed(tmp_path: Path) -> None:
    with ctzd.testing.fake_store(tmp_path):
        with pytest.raises(ctzd.StoreError, match="allow_empty"):
            ctzd.write_table(frame(), "prices", now=lambda: at(0))
        ctzd.write_table(frame(), "prices", allow_empty=True, now=lambda: at(0))
        assert ctzd.read_table("prices").empty


def test_failed_write_leaves_the_published_table_unchanged(tmp_path: Path) -> None:
    unwritable = pd.DataFrame({"payload": [object()]})  # parquet cannot store a bare object
    with ctzd.testing.fake_store(tmp_path):
        ctzd.write_table(frame(1.0), "prices", now=lambda: at(0))
        with pytest.raises(Exception, match="object"):
            ctzd.write_table(unwritable, "prices", now=lambda: at(1))
        assert ctzd.read_table("prices")["price"].tolist() == [1.0]


def test_only_the_newest_versions_are_kept(tmp_path: Path) -> None:
    with ctzd.testing.fake_store(tmp_path, sbu="fi", app="bonds"):
        for minute in range(4):
            ctzd.write_table(frame(float(minute)), "prices", keep=2, now=lambda minute=minute: at(minute))
    versions = sorted(path.name for path in (tmp_path / "fi" / "bonds" / "prices").glob("v=*"))
    assert versions == ["v=20261104T070200000000Z", "v=20261104T070300000000Z"]


def test_another_apps_table_is_read_by_name(tmp_path: Path) -> None:
    with ctzd.testing.fake_store(tmp_path, sbu="fi", app="loader"):
        ctzd.write_table(frame(5.0), "prices", now=lambda: at(0))
    with ctzd.testing.fake_store(tmp_path, sbu="fi", app="dashboard"):
        assert ctzd.read_table("prices", app="loader")["price"].tolist() == [5.0]


def test_reading_a_table_that_was_never_written_says_so(tmp_path: Path) -> None:
    with ctzd.testing.fake_store(tmp_path), pytest.raises(ctzd.StoreError, match="never been written"):
        ctzd.read_table("prices")


def test_naive_timestamps_are_refused(tmp_path: Path) -> None:
    with ctzd.testing.fake_store(tmp_path), pytest.raises(ctzd.StoreError, match="timezone"):
        ctzd.write_table(frame(1.0), "prices", now=lambda: dt.datetime(2026, 11, 4))  # noqa: DTZ001 - the naive value is the point of the test.


@pytest.mark.parametrize("url", ["s3://bucket/prefix", "abfs://account.dfs.core.windows.net/prefix"])
def test_unsupported_store_urls_are_rejected(url: str) -> None:
    with pytest.raises(ctzd.StoreError):
        storage_for(url, None)
