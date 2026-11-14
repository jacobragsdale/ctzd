"""The table store: a job publishes versions of a table, a page reads the latest, and sample rows leave their mark."""

import datetime as dt
import os
from pathlib import Path

import pandas as pd
import pytest

from ctzd_common import samples
from ctzd_kit import StoreError, store, testing
from ctzd_kit.store import SAMPLE_ATTR, storage_for


@pytest.fixture(autouse=True)
def desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop with nothing faked, no source's secret, and no sample rows served yet."""
    for name in list(os.environ):
        if name.startswith("CTZD_"):
            monkeypatch.delenv(name)
    samples.reset()


def frame(*prices: float) -> pd.DataFrame:
    return pd.DataFrame({"isin": [f"X{i}" for i in range(len(prices))], "price": list(prices)})


def at(minute: int) -> dt.datetime:
    return dt.datetime(2026, 11, 13, 7, minute, tzinfo=dt.UTC)


def test_a_table_reads_back_as_written_with_every_number_a_float(tmp_path: Path) -> None:
    written = pd.DataFrame({"sector": ["Banks", "Gov"], "bonds": [42, 7], "as_of": pd.to_datetime(["2026-11-13", "2026-11-13"])})
    with testing.store(tmp_path):
        store.write_table(written, "sector_summary", now=lambda: at(0))
        summary = store.read_table("sector_summary")
    assert summary["bonds"].dtype == "float64"
    assert summary.to_dict("list") == {"sector": ["Banks", "Gov"], "bonds": [42.0, 7.0], "as_of": [pd.Timestamp("2026-11-13")] * 2}
    assert SAMPLE_ATTR not in summary.attrs
    assert written["bonds"].dtype == "int64", "the caller's frame is left as it was"


def test_read_returns_the_latest_version_and_columns_limit_it(tmp_path: Path) -> None:
    with testing.store(tmp_path):
        store.write_table(frame(1.0), "prices", now=lambda: at(0))
        version = store.write_table(frame(2.0, 3.0), "prices", now=lambda: at(1))
        assert store.read_table("prices")["price"].tolist() == [2.0, 3.0]
        assert store.read_table("prices", columns=["price"]).columns.tolist() == ["price"]
    assert version == "20261113T070100000000Z"
    assert (tmp_path / "test" / "test" / "prices" / "_latest").read_text(encoding="utf-8") == version


def test_an_empty_frame_is_refused_unless_allowed(tmp_path: Path) -> None:
    with testing.store(tmp_path):
        with pytest.raises(StoreError, match="allow_empty"):
            store.write_table(frame(), "prices", now=lambda: at(0))
        store.write_table(frame(), "prices", allow_empty=True, now=lambda: at(0))
        assert store.read_table("prices").empty


def test_only_the_newest_versions_are_kept(tmp_path: Path) -> None:
    with testing.store(tmp_path, sbu="fi", app="bonds"):
        for minute in range(4):
            store.write_table(frame(float(minute)), "prices", keep=2, now=lambda minute=minute: at(minute))
        assert store.read_table("prices")["price"].tolist() == [3.0]
    versions = sorted(path.name for path in (tmp_path / "fi" / "bonds" / "prices").glob("v=*"))
    assert versions == ["v=20261113T070200000000Z", "v=20261113T070300000000Z"]


def test_a_failed_write_leaves_the_published_table_as_it_was(tmp_path: Path) -> None:
    with testing.store(tmp_path):
        store.write_table(frame(1.0), "prices", now=lambda: at(0))
        with pytest.raises(ValueError, match="object"):
            store.write_table(pd.DataFrame({"payload": [object()]}), "prices", now=lambda: at(1))
        assert store.read_table("prices")["price"].tolist() == [1.0]


def test_a_table_written_from_sample_rows_carries_the_mark_to_its_reader(tmp_path: Path) -> None:
    samples.mark("fi_pricing")
    samples.mark("sample")
    with testing.store(tmp_path):
        version = store.write_table(frame(1.0), "prices", now=lambda: at(0))
        samples.reset()
        read = store.read_table("prices")
    assert (tmp_path / "test" / "test" / "prices" / f"v={version}" / "_sample").read_text(encoding="utf-8") == "fi_pricing,sample"
    assert read.attrs[SAMPLE_ATTR] is True
    assert samples.served() == {"fi_pricing", "sample"}


@pytest.mark.parametrize("name", ["Prices", "1prices", "prices-today", "../prices", ""])
def test_a_table_name_is_lowercase_letters_digits_and_underscores(name: str, tmp_path: Path) -> None:
    with testing.store(tmp_path), pytest.raises(StoreError, match="Invalid table name"):
        store.write_table(frame(1.0), name)


def test_a_table_never_written_says_how_to_write_it(tmp_path: Path) -> None:
    with testing.store(tmp_path), pytest.raises(StoreError, match="never been written"):
        store.read_table("prices")


def test_a_naive_clock_and_keep_below_one_are_refused(tmp_path: Path) -> None:
    with testing.store(tmp_path):
        with pytest.raises(StoreError, match="timezone"):
            store.write_table(frame(1.0), "prices", now=lambda: dt.datetime(2026, 11, 13))  # noqa: DTZ001 - the naive value is the point
        with pytest.raises(StoreError, match="keep"):
            store.write_table(frame(1.0), "prices", keep=0)


def test_the_store_follows_the_apps_settings(tmp_path: Path) -> None:
    with testing.given({"CTZD_STORE_URL": str(tmp_path / "store"), "CTZD_SBU": "fi", "CTZD_APP": "bonds"}):
        store.write_table(frame(5.0), "prices", now=lambda: at(0))
        assert store.read_table("prices")["price"].tolist() == [5.0]
    assert (tmp_path / "store" / "fi" / "bonds" / "prices" / "_latest").is_file()


def test_outside_an_app_the_store_says_how_to_run(tmp_path: Path) -> None:
    with testing.given({"CTZD_STORE_URL": str(tmp_path), "CTZD_APP": ""}), pytest.raises(StoreError, match="ctzd exec"):
        store.read_table("prices")


@pytest.mark.parametrize("url", ["s3://bucket/prefix", "abfs://account.dfs.core.windows.net/prefix"])
def test_unsupported_store_urls_are_refused(url: str) -> None:
    with pytest.raises(StoreError):
        storage_for(url)


def test_a_file_url_is_a_local_folder(tmp_path: Path) -> None:
    assert storage_for(f"file://{tmp_path.as_posix()}/store").root == (tmp_path / "store").as_posix()
    assert (tmp_path / "store").is_dir()


@pytest.mark.parametrize(
    ("url", "folder"),
    [("C:\\Users\\ann\\app\\.ctzd\\out\\store", "C:\\Users\\ann\\app\\.ctzd\\out\\store"), ("relative/store", "relative/store"), ("file:///srv/store", "/srv/store"), ("https://x.invalid/s", None)],
)
def test_a_windows_path_is_a_folder_not_a_url_scheme(url: str, folder: str | None) -> None:
    from ctzd_kit.frames import local_path  # noqa: PLC0415 - the one test of this helper

    found = local_path(url)
    assert found == (None if folder is None else str(Path(folder)))
