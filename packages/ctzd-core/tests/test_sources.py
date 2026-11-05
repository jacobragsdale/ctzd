import json

import pytest

from ctzd import SourceError
from ctzd.sources import MssqlSource, OracleSource, RestSource, env_var, load_source

ORACLE = {"kind": "oracle", "dsn": "db.contoso.com:1521/FIPRD", "user": "ctzd_ro", "password": "pw-oracle-1"}


def test_env_var_upper_cases_the_source_name() -> None:
    assert env_var("fi_pricing") == "CTZD_SOURCE_FI_PRICING"


@pytest.mark.parametrize("name", ["", "Fi", "1fi", "fi-pricing", "a" * 64])
def test_invalid_source_names_are_rejected(name: str) -> None:
    with pytest.raises(SourceError, match="Invalid source name"):
        env_var(name)


def test_oracle_source_loads_from_its_variable() -> None:
    source = load_source("fi_pricing", {"CTZD_SOURCE_FI_PRICING": json.dumps(ORACLE)})
    assert isinstance(source, OracleSource)
    assert source.dsn == "db.contoso.com:1521/FIPRD"


def test_rest_source_with_client_credentials_loads() -> None:
    raw = {"kind": "rest", "base_url": "https://api.contoso.com/v2", "auth": {"type": "client_credentials", "token_url": "https://login.contoso.com/token", "client_id": "c", "client_secret": "s"}}
    source = load_source("fi_api", {"CTZD_SOURCE_FI_API": json.dumps(raw)})
    assert isinstance(source, RestSource)


def test_missing_source_says_how_to_configure_it() -> None:
    with pytest.raises(SourceError, match="ctzd env"):
        load_source("fi_pricing", {})


def test_invalid_description_never_echoes_the_password() -> None:
    raw = json.dumps({**ORACLE, "kind": "mssql"})  # mssql needs server and database
    with pytest.raises(SourceError) as caught:
        load_source("fi_pricing", {"CTZD_SOURCE_FI_PRICING": raw})
    assert "pw-oracle-1" not in str(caught.value)
    assert "server" in str(caught.value)


def test_unknown_fields_are_rejected() -> None:
    raw = json.dumps({**ORACLE, "port": 1521})
    with pytest.raises(SourceError, match="port"):
        load_source("fi_pricing", {"CTZD_SOURCE_FI_PRICING": raw})


def test_mssql_defaults_to_encrypted_connections() -> None:
    raw = {"kind": "mssql", "server": "sql.contoso.com", "database": "fi", "user": "ro", "password": "x"}
    source = load_source("fi_sql", {"CTZD_SOURCE_FI_SQL": json.dumps(raw)})
    assert isinstance(source, MssqlSource)
    assert source.encrypt
    assert not source.trust_server_certificate
