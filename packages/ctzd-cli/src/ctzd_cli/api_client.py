"""Client for the platform API. Every call carries the person's GitHub token."""

import contextlib
from typing import Protocol

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from ctzd_cli import api_models as m
from ctzd_cli.errors import CliError, PlatformUnavailableError

_TIMEOUT = 60.0
# Every command in an app asks for the current platform release; an unreachable platform must not stall it.
_CURRENT_TIMEOUT = 5.0
_DETAIL: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


class Platform(Protocol):
    def me(self) -> m.Me: ...
    def create_app(self, request: m.CreateAppRequest) -> m.CreatedApp: ...
    def status(self, sbu: str, name: str) -> m.AppStatus: ...
    def resume(self, sbu: str, name: str) -> None: ...
    def build_for_commit(self, sbu: str, name: str, commit: str) -> m.Build: ...
    def screenshot(self, sbu: str, name: str, number: int) -> bytes: ...
    def rollback(self, sbu: str, name: str, release: int | None) -> m.RollbackAccepted: ...
    def run_job(self, sbu: str, name: str, job: str) -> m.JobRunCreated: ...
    def job_run(self, sbu: str, name: str, job: str, run_id: str) -> m.JobRunState: ...
    def sources(self) -> m.SourceList: ...
    def logs(self, sbu: str, name: str, *, job: str | None, tail: int) -> m.LogText: ...
    def env(self, sbu: str, name: str, sources: tuple[str, ...]) -> m.EnvVars: ...
    def delete(self, sbu: str, name: str) -> None: ...
    def current_release(self, sbu: str | None, name: str | None) -> m.CurrentRelease: ...
    def release(self, number: int) -> m.PlatformRelease: ...


class PlatformClient:
    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/") + "/v1", timeout=_TIMEOUT, transport=transport, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _call(self, method: str, path: str, *, body: BaseModel | None = None, params: dict[str, str | int] | None = None, timeout: float = _TIMEOUT) -> httpx.Response:
        try:
            response = self._http.request(method, path, json=None if body is None else body.model_dump(mode="json"), params=params, timeout=timeout)
        except httpx.TransportError as err:
            msg = "The ctzd platform could not be reached."
            raise PlatformUnavailableError(msg, hint="Check the network connection and try again in a minute.") from err
        if response.is_error:
            raise _error(response)
        return response

    def _get[T: BaseModel](self, model: type[T], method: str, path: str, *, body: BaseModel | None = None, params: dict[str, str | int] | None = None) -> T:
        return _parse(model, self._call(method, path, body=body, params=params), method, path)

    def me(self) -> m.Me:
        return self._get(m.Me, "GET", "/me")

    def create_app(self, request: m.CreateAppRequest) -> m.CreatedApp:
        return self._get(m.CreatedApp, "POST", "/apps", body=request)

    def status(self, sbu: str, name: str) -> m.AppStatus:
        return self._get(m.AppStatus, "GET", f"/apps/{sbu}/{name}")

    def resume(self, sbu: str, name: str) -> None:
        self._call("POST", f"/apps/{sbu}/{name}/resume")

    def build_for_commit(self, sbu: str, name: str, commit: str) -> m.Build:
        return self._get(m.Build, "GET", f"/apps/{sbu}/{name}/builds", params={"commit": commit})

    def screenshot(self, sbu: str, name: str, number: int) -> bytes:
        return self._call("GET", f"/apps/{sbu}/{name}/builds/{number}/screenshot").content

    def rollback(self, sbu: str, name: str, release: int | None) -> m.RollbackAccepted:
        return self._get(m.RollbackAccepted, "POST", f"/apps/{sbu}/{name}/rollback", body=m.RollbackRequest(release=release))

    def run_job(self, sbu: str, name: str, job: str) -> m.JobRunCreated:
        return self._get(m.JobRunCreated, "POST", f"/apps/{sbu}/{name}/jobs/{job}/runs")

    def job_run(self, sbu: str, name: str, job: str, run_id: str) -> m.JobRunState:
        return self._get(m.JobRunState, "GET", f"/apps/{sbu}/{name}/jobs/{job}/runs/{run_id}")

    def sources(self) -> m.SourceList:
        return self._get(m.SourceList, "GET", "/sources")

    def logs(self, sbu: str, name: str, *, job: str | None, tail: int) -> m.LogText:
        params: dict[str, str | int] = {"tail": tail}
        if job is not None:
            params["job"] = job
        return self._get(m.LogText, "GET", f"/apps/{sbu}/{name}/logs", params=params)

    def env(self, sbu: str, name: str, sources: tuple[str, ...]) -> m.EnvVars:
        return self._get(m.EnvVars, "POST", f"/apps/{sbu}/{name}/env", body=m.EnvRequest(sources=sources))

    def delete(self, sbu: str, name: str) -> None:
        self._call("DELETE", f"/apps/{sbu}/{name}")

    def current_release(self, sbu: str | None, name: str | None) -> m.CurrentRelease:
        """The release of an app's channel; with no app, the general channel's."""
        path = "/releases/current"
        params: dict[str, str | int] = {} if sbu is None or name is None else {"sbu": sbu, "app": name}
        return _parse(m.CurrentRelease, self._call("GET", path, params=params, timeout=_CURRENT_TIMEOUT), "GET", path)

    def release(self, number: int) -> m.PlatformRelease:
        return self._get(m.PlatformRelease, "GET", f"/releases/{number}")


def _parse[T: BaseModel](model: type[T], response: httpx.Response, method: str, path: str) -> T:
    try:
        return model.model_validate_json(response.content)
    except ValidationError as err:
        msg = f"The platform returned an unexpected reply to {method} {path}."
        raise CliError(msg) from err


def _error(response: httpx.Response) -> CliError:
    detail: object = response.text
    with contextlib.suppress(ValidationError):
        detail = _DETAIL.validate_json(response.content).get("detail", detail)
    if response.status_code == httpx.codes.UNAUTHORIZED:
        return PlatformUnavailableError("The platform did not accept your GitHub login.", hint="Run `ctzd login --start` to log in again.")
    if response.status_code == httpx.codes.FORBIDDEN:
        return CliError(f"You do not have access: {detail}", hint="Ask the app's owner or your SBU champion to add you to the repo.")
    return CliError(f"The platform refused the request ({response.status_code}): {detail}")
