"""`ctzd.rest`: an HTTP client for a named REST source with auth, timeouts, retries, and paging."""

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import assert_never

import httpx
import pandas as pd
from pydantic import TypeAdapter, ValidationError

from ctzd import _overrides
from ctzd._errors import DataError, SourceError
from ctzd.sources import BasicAuth, BearerAuth, ClientCredentialsAuth, HeaderAuth, NoAuth, RestSource, check_name, load_source

_LOG = logging.getLogger("ctzd.rest")
QueryParams = Mapping[str, str | int | float | bool]
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_ATTEMPTS = 3
_MAX_PAGES = 1_000
_TOKEN_MARGIN_SECONDS = 60.0
_FAKE_BASE_URL = "https://fake.ctzd.invalid"
_OBJECT: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_ROWS: TypeAdapter[list[dict[str, object]]] = TypeAdapter(list[dict[str, object]])


def _as_object(payload: object) -> dict[str, object] | None:
    try:
        return _OBJECT.validate_python(payload)
    except ValidationError:
        return None


class RestClient:
    def __init__(self, source: RestSource, *, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self._source = source
        self._http = httpx.Client(base_url=str(source.base_url).rstrip("/"), timeout=source.timeout_seconds, transport=transport)
        self._sleep = sleep
        self._clock = clock
        self._token: tuple[str, float] | None = None
        self._token_lock = threading.Lock()

    def close(self) -> None:
        self._http.close()

    def get_json(self, path: str, params: QueryParams | None = None) -> object:
        return self._request("GET", path, params=params, body=None).json()

    def post_json(self, path: str, body: object, params: QueryParams | None = None) -> object:
        return self._request("POST", path, params=params, body=body).json()

    def get_frame(self, path: str, params: QueryParams | None = None, *, records: str | None = None) -> pd.DataFrame:
        """GET `path` and turn the JSON rows into a frame.

        `records` names the key holding the list of rows when the response is an
        object rather than a list. Nested objects become dotted column names.
        """
        return records_frame(self.get_json(path, params), records)

    def get_all_pages(self, path: str, params: QueryParams | None = None, *, records: str, next_key: str = "next") -> pd.DataFrame:
        """Follow the URL in `next_key` of each response until it is empty, and concatenate the rows."""
        frames: list[pd.DataFrame] = []
        url: str | None = path
        page_params = params
        for _ in range(_MAX_PAGES):
            if url is None:
                break
            payload = self.get_json(url, page_params)
            frames.append(records_frame(payload, records))
            body = _as_object(payload)
            following = None if body is None else body.get(next_key)
            url = str(following) if following else None
            page_params = None  # the next link carries its own query string
        else:
            msg = f"{path} returned more than {_MAX_PAGES} pages; narrow the request."
            raise DataError(msg)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _request(self, method: str, path: str, *, params: QueryParams | None, body: object) -> httpx.Response:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                response = self._http.request(method, path, params=None if params is None else dict(params), json=body, headers=self._headers())
            except httpx.TransportError:
                if attempt == _ATTEMPTS:
                    raise
                _LOG.warning("%s %s failed to connect (attempt %d), retrying", method, path, attempt)
                self._sleep(float(2**attempt))
                continue
            if response.status_code in _RETRY_STATUS and attempt < _ATTEMPTS:
                _LOG.warning("%s %s returned %d (attempt %d), retrying", method, path, response.status_code, attempt)
                self._sleep(float(2**attempt))
                continue
            response.raise_for_status()
            return response
        msg = "unreachable: the retry loop always returns or raises"
        raise AssertionError(msg)

    def _headers(self) -> dict[str, str]:
        auth = self._source.auth
        match auth:
            case NoAuth():
                return {}
            case BearerAuth():
                return {"Authorization": f"Bearer {auth.token.get_secret_value()}"}
            case HeaderAuth():
                return {auth.name: auth.value.get_secret_value()}
            case BasicAuth():
                request = httpx.Request("GET", "https://basic.invalid")
                flow = httpx.BasicAuth(auth.username, auth.password.get_secret_value()).auth_flow(request)
                return {"Authorization": next(flow).headers["Authorization"]}
            case ClientCredentialsAuth():
                return {"Authorization": f"Bearer {self._client_credentials_token(auth)}"}
            case _:
                assert_never(auth)

    def _client_credentials_token(self, auth: ClientCredentialsAuth) -> str:
        with self._token_lock:
            now = self._clock()
            if self._token is not None and self._token[1] > now:
                return self._token[0]
            form = {"grant_type": "client_credentials", "client_id": auth.client_id, "client_secret": auth.client_secret.get_secret_value()}
            if auth.scope is not None:
                form["scope"] = auth.scope
            response = self._http.post(str(auth.token_url), data=form)
            response.raise_for_status()
            body = _as_object(response.json()) or {}
            token = body.get("access_token")
            lifetime = body.get("expires_in", 300)
            if not isinstance(token, str) or not isinstance(lifetime, int | float):
                msg = "The token endpoint did not return access_token and expires_in."
                raise SourceError(msg)
            self._token = (token, now + float(lifetime) - _TOKEN_MARGIN_SECONDS)
            return token


def records_frame(payload: object, records: str | None) -> pd.DataFrame:
    """Rows from a JSON list, or from the list under `records` in a JSON object."""
    rows = payload
    if records is not None:
        body = _as_object(payload)
        if body is None or records not in body:
            msg = f"Expected a JSON object with a {records!r} key."
            raise DataError(msg)
        rows = body[records]
    try:
        normalized = _ROWS.validate_python(rows)
    except ValidationError:
        msg = "Expected a JSON list of objects; pass records= to name the key that holds the rows."
        raise DataError(msg) from None
    return pd.json_normalize(normalized)


_clients: dict[tuple[str, str], RestClient] = {}
_clients_lock = threading.Lock()


def rest(source: str) -> RestClient:
    """Return the client for `source`; clients are reused so connections stay open between calls."""
    check_name(source)
    fakes = _overrides.current().rest
    if fakes is not None:
        return _fake_client(source, fakes)
    raw = os.environ.get(f"CTZD_SOURCE_{source.upper()}", "")
    with _clients_lock:
        client = _clients.get((source, raw))
        if client is None:
            config = load_source(source, os.environ)
            if not isinstance(config, RestSource):
                msg = f"Source {source!r} is a {config.kind} source; call ctzd.query for databases."
                raise SourceError(msg)
            client = RestClient(config)
            _clients[(source, raw)] = client
        return client


def _fake_client(source: str, fakes: Mapping[str, Mapping[str, object]]) -> RestClient:
    routes = fakes.get(source)
    if routes is None:
        msg = f"ctzd.testing.fake_sources has no fake for source {source!r}; faked sources: {sorted(fakes)}."
        raise SourceError(msg)

    def handle(request: httpx.Request) -> httpx.Response:
        route = f"{request.method} {request.url.path}"
        if route not in routes:
            return httpx.Response(404, json={"error": f"no fake for {route}", "known": sorted(routes)})
        result = routes[route]
        payload = result.function(request) if isinstance(result, _overrides.RestHandler) else result
        return httpx.Response(200, json=payload)

    config = RestSource.model_validate({"kind": "rest", "base_url": _FAKE_BASE_URL, "auth": {"type": "none"}})
    return RestClient(config, transport=httpx.MockTransport(handle), sleep=lambda _seconds: None)
