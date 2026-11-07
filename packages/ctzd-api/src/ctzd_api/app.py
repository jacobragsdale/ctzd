"""The platform API: thin FastAPI routes over `service`. Handlers are `def` because the gateways block."""

import threading
import time
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ctzd_api import service
from ctzd_api.service import ApiError, AppRef, Services
from ctzd_cli import api_models as m

_LOGIN_CACHE_SECONDS = 300.0


class LoginCache:
    """Remember which GitHub login a token belongs to, so each call does not ask GitHub again."""

    def __init__(self, resolve: Callable[[str], str | None], clock: Callable[[], float] = time.monotonic) -> None:
        self._resolve = resolve
        self._clock = clock
        self._entries: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def login(self, token: str) -> str | None:
        now = self._clock()
        with self._lock:
            hit = self._entries.get(token)
            if hit is not None and hit[1] > now:
                return hit[0]
        login = self._resolve(token)
        if login is not None:
            with self._lock:
                self._entries[token] = (login, now + _LOGIN_CACHE_SECONDS)
        return login


def current_login(request: Request, authorization: Annotated[str | None, Header()] = None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Send the GitHub token as `Authorization: Bearer TOKEN`.")
    logins = request.app.state.logins
    if not isinstance(logins, LoginCache):
        msg = "create_app did not install the login cache"
        raise TypeError(msg)
    login = logins.login(authorization.removeprefix("Bearer ").strip())
    if login is None:
        raise HTTPException(status_code=401, detail="GitHub did not accept the token.")
    return login


Login = Annotated[str, Depends(current_login)]


def _api_error(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, ApiError):
        raise error
    return JSONResponse(status_code=error.status, content={"detail": error.detail})


def create_app(services: Services) -> FastAPI:
    api = FastAPI(title="ctzd platform API", version="1")
    api.state.logins = LoginCache(services.github.login_for)
    api.add_exception_handler(ApiError, _api_error)

    @api.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/v1/me")
    def me(login: Login) -> m.Me:
        return m.Me(login=login)

    _platform_routes(api, services)
    _app_routes(api, services)
    return api


def _platform_routes(api: FastAPI, services: Services) -> None:
    """Calls that are not about one app."""

    @api.get("/v1/releases/current")
    def current_release(_login: Login, sbu: str | None = None, app: str | None = None) -> m.CurrentRelease:
        if (sbu is None) != (app is None):
            raise HTTPException(status_code=400, detail="Pass both sbu and app, or neither for the general channel.")
        return service.current_release(services, None if sbu is None or app is None else AppRef(sbu, app))

    @api.get("/v1/releases/{number}")
    def platform_release(_login: Login, number: int) -> m.PlatformRelease:
        return service.platform_release(services, number)

    @api.get("/v1/sources")
    def sources(_login: Login) -> m.SourceList:
        return service.sources(services)


def _app_routes(api: FastAPI, services: Services) -> None:
    @api.post("/v1/apps", status_code=201)
    def create(login: Login, request: m.CreateAppRequest) -> m.CreatedApp:
        return service.create_app(services, login, request)

    @api.get("/v1/apps/{sbu}/{name}")
    def status(login: Login, sbu: str, name: str) -> m.AppStatus:
        return service.status(services, login, AppRef(sbu, name))

    @api.post("/v1/apps/{sbu}/{name}/resume", status_code=204)
    def resume(login: Login, sbu: str, name: str) -> Response:
        service.resume(services, login, AppRef(sbu, name))
        return Response(status_code=204)

    @api.get("/v1/apps/{sbu}/{name}/builds")
    def build(login: Login, sbu: str, name: str, commit: str) -> m.Build:
        return service.build_for_commit(services, login, AppRef(sbu, name), commit)

    @api.get("/v1/apps/{sbu}/{name}/builds/{number}/screenshot")
    def screenshot(login: Login, sbu: str, name: str, number: int) -> Response:
        return Response(content=service.screenshot(services, login, AppRef(sbu, name), number), media_type="image/png")

    @api.post("/v1/apps/{sbu}/{name}/rollback")
    def rollback(login: Login, sbu: str, name: str, request: m.RollbackRequest) -> m.RollbackAccepted:
        return service.rollback(services, login, AppRef(sbu, name), request.release)

    @api.post("/v1/apps/{sbu}/{name}/jobs/{job}/runs")
    def run_job(login: Login, sbu: str, name: str, job: str) -> m.JobRunCreated:
        return service.run_job(services, login, AppRef(sbu, name), job)

    @api.get("/v1/apps/{sbu}/{name}/jobs/{job}/runs/{run_id}")
    def job_run(login: Login, sbu: str, name: str, job: str, run_id: str) -> m.JobRunState:
        return service.job_run(services, login, AppRef(sbu, name), job, run_id)

    @api.get("/v1/apps/{sbu}/{name}/logs")
    def logs(login: Login, sbu: str, name: str, tail: int = 200, job: str | None = None) -> m.LogText:
        return service.logs(services, login, AppRef(sbu, name), job=job, tail=max(1, min(tail, 5_000)))

    @api.post("/v1/apps/{sbu}/{name}/env")
    def env(login: Login, sbu: str, name: str, request: m.EnvRequest) -> m.EnvVars:
        return service.env(services, login, AppRef(sbu, name), request.sources)

    @api.delete("/v1/apps/{sbu}/{name}", status_code=204)
    def delete(login: Login, sbu: str, name: str) -> Response:
        service.delete(services, login, AppRef(sbu, name))
        return Response(status_code=204)
