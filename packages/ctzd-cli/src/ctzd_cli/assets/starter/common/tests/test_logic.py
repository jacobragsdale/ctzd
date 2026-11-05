import pandas as pd
import pytest

from app.logic.summary import with_share


def test_share_is_each_value_over_the_total() -> None:
    frame = with_share(pd.DataFrame({"category": ["a", "b"], "value": [3.0, 1.0]}))
    assert frame["share"].tolist() == [0.75, 0.25]


def test_share_is_zero_when_the_total_is_zero() -> None:
    frame = with_share(pd.DataFrame({"category": ["a"], "value": [0.0]}))
    assert frame["share"].tolist() == [0.0]


def test_empty_frame_stays_empty() -> None:
    frame = with_share(pd.DataFrame({"category": pd.Series([], dtype="str"), "value": pd.Series([], dtype="float64")}))
    assert frame.empty
    assert "share" in frame.columns


@pytest.mark.parametrize("values", [[1.0], [2.0, 2.0, 4.0]])
def test_shares_add_up_to_one(values: list[float]) -> None:
    frame = with_share(pd.DataFrame({"category": [str(i) for i in range(len(values))], "value": values}))
    assert frame["share"].sum() == pytest.approx(1.0, abs=1e-12)
