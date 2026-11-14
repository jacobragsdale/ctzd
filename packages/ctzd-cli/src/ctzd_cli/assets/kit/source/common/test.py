"""{{source}}'s loaders answer their sample rows on the fakes."""

from datetime import date

from ctzd_kit import testing

from {{kit}} import {{source}}


def test_example_answers_its_sample_rows() -> None:
    with testing.fakes():
        rows = {{source}}.example(date(2026, 11, 13))
    assert list(rows.columns) == ["item_id", "as_of", "name", "value"]
    assert rows["value"].dtype == "float64"
