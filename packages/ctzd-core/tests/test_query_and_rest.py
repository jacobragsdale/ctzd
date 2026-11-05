import httpx
import pandas as pd
import pytest
from pydantic import SecretStr

import ctzd
from ctzd.rest import RestClient, records_frame
from ctzd.sources import BearerAuth, ClientCredentialsAuth, RestSource

LOGIN_URL = "https://login.contoso.com/token"


def test_fake_sql_source_returns_its_frame() -> None:
    prices = pd.DataFrame({"isin": ["X1"], "price": [101.5]})
    with ctzd.testing.fake_sources(fi_pricing={"sql/prices.sql": prices}):
        assert ctzd.query("fi_pricing", "sql/prices.sql")["price"].tolist() == [101.5]


def test_fake_sql_source_can_depend_on_parameters() -> None:
    by_day = ctzd.testing.by_params(lambda params: pd.DataFrame({"day": [params["day"]]}))
    with ctzd.testing.fake_sources(fi_pricing={"sql/prices.sql": by_day}):
        assert ctzd.query("fi_pricing", "sql/prices.sql", {"day": "2026-11-04"})["day"].tolist() == ["2026-11-04"]


def test_unfaked_source_fails_inside_fake_sources() -> None:
    with ctzd.testing.fake_sources(fi_pricing={"sql/prices.sql": pd.DataFrame()}), pytest.raises(ctzd.SourceError, match="no fake"):
        ctzd.query("fi_trades", "sql/trades.sql")


def test_unknown_sql_file_lists_the_known_ones() -> None:
    with ctzd.testing.fake_sources(fi_pricing={"sql/prices.sql": pd.DataFrame()}), pytest.raises(ctzd.SourceError, match=r"prices\.sql"):
        ctzd.query("fi_pricing", "sql/other.sql")


def test_fake_rest_source_serves_json_rows_as_a_frame() -> None:
    payload = {"items": [{"isin": "X1", "issuer": {"name": "Contoso"}}]}
    with ctzd.testing.fake_sources(fi_api={"GET /bonds": payload}):
        bonds = ctzd.rest("fi_api").get_frame("/bonds", records="items")
    assert bonds.columns.tolist() == ["isin", "issuer.name"]


def test_records_frame_requires_a_list_of_objects() -> None:
    with pytest.raises(ctzd.DataError, match="records="):
        records_frame({"items": []}, None)


def test_pages_are_followed_until_next_is_empty() -> None:
    pages = {"/bonds": {"items": [{"n": 1}], "next": "https://api.contoso.com/bonds?page=2"}, "/bonds?page=2": {"items": [{"n": 2}], "next": None}}

    def handle(request: httpx.Request) -> httpx.Response:
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        return httpx.Response(200, json=pages[key])

    client = RestClient(_source(), transport=httpx.MockTransport(handle))
    assert client.get_all_pages("/bonds", records="items")["n"].tolist() == [1, 2]


def test_server_errors_are_retried_then_raised() -> None:
    attempts: list[int] = []

    def handle(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503)

    client = RestClient(_source(), transport=httpx.MockTransport(handle), sleep=lambda _s: None)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("/bonds")
    assert len(attempts) == 3


def test_a_retry_that_succeeds_returns_the_payload() -> None:
    statuses = iter([502, 200])

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), json={"ok": True})

    client = RestClient(_source(), transport=httpx.MockTransport(handle), sleep=lambda _s: None)
    assert client.get_json("/bonds") == {"ok": True}


def test_client_credentials_token_is_reused_until_it_nears_expiry() -> None:
    clock = [0.0]
    issued: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            issued.append(1)
            return httpx.Response(200, json={"access_token": f"t{len(issued)}", "expires_in": 3600})
        return httpx.Response(200, json={"auth": request.headers["Authorization"]})

    auth = ClientCredentialsAuth.model_validate({"type": "client_credentials", "token_url": LOGIN_URL, "client_id": "c", "client_secret": "s"})
    client = RestClient(_source(auth), transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    assert client.get_json("/a") == {"auth": "Bearer t1"}
    clock[0] = 3000.0
    assert client.get_json("/a") == {"auth": "Bearer t1"}
    clock[0] = 3541.0  # inside the 60-second safety margin
    assert client.get_json("/a") == {"auth": "Bearer t2"}


def test_query_refuses_a_rest_source() -> None:
    with ctzd.testing.fake_sources(fi_api={"GET /bonds": []}), pytest.raises(ctzd.SourceError, match="no fake"):
        ctzd.query("fi_api", "sql/x.sql")


def _source(auth: BearerAuth | ClientCredentialsAuth | None = None) -> RestSource:
    return RestSource(kind="rest", base_url="https://api.contoso.com", auth=auth or BearerAuth(type="bearer", token=SecretStr("t")))
