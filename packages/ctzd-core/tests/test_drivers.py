import pytest
from pydantic import SecretStr

from ctzd._mssql import connection_string
from ctzd._oracle import _column_name
from ctzd.sources import MssqlSource


def _source(password: str) -> MssqlSource:
    return MssqlSource(kind="mssql", server="sql.contoso.com,1433", database="fi", user="ro", password=SecretStr(password))


@pytest.mark.parametrize(("password", "rendered"), [("plain", "Pwd=plain;"), ("semi;colon", "Pwd={semi;colon};"), ("brace}d", "Pwd={brace}}d};"), (" padded", "Pwd={ padded};")])
def test_connection_string_quotes_values_odbc_would_misread(password: str, rendered: str) -> None:
    assert rendered in connection_string(_source(password))


def test_connection_string_encrypts_by_default() -> None:
    assert connection_string(_source("x")).endswith("Encrypt=yes;TrustServerCertificate=no")


@pytest.mark.parametrize(("reported", "expected"), [("ISIN", "isin"), ("AS_OF", "as_of"), ("MixedCase", "MixedCase"), ("lower", "lower"), ("PRICE2", "price2")])
def test_oracle_upper_case_identifiers_become_lower_case(reported: str, expected: str) -> None:
    assert _column_name(reported) == expected
