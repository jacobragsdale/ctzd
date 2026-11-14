"""GitHub login with the OAuth device flow. The token authenticates git and the CLI's calls to GitHub.

The flow is split into `start` and `wait` because agents see a command's output
only when it ends: `ctzd login --start` returns the code to show the person,
and `ctzd login --wait` blocks until they have approved it.
"""

import json
import os
import time
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import keyring
import keyring.errors
from pydantic import BaseModel, ConfigDict

from ctzd_cli import config
from ctzd_cli.errors import CliError

LOGIN_VARIABLE = "CTZD_GITHUB_TOKEN"
_KEYRING_SERVICE = "ctzd"
_SCOPES = "repo read:org"
_TIMEOUT = 30.0
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

_CONFIG = ConfigDict(extra="ignore", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class Profile(BaseModel):
    model_config = _CONFIG
    login: str
    name: str | None
    email: str | None


class PendingLogin(BaseModel):
    model_config = _CONFIG
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_at: datetime


class _DeviceCode(BaseModel):
    model_config = _CONFIG
    device_code: str
    user_code: str
    verification_uri: str
    interval: int = 5
    expires_in: int = 900


class _TokenReply(BaseModel):
    model_config = _CONFIG
    access_token: str | None = None
    error: str | None = None
    interval: int | None = None


def token() -> str | None:
    """The stored token; CTZD_GITHUB_TOKEN wins, which is how Jenkins and tests supply one."""
    from_env = os.environ.get(LOGIN_VARIABLE, "")
    if from_env:
        return from_env
    try:
        return keyring.get_password(_KEYRING_SERVICE, config.load().github.host)
    except keyring.errors.KeyringError:
        return None


def require_token() -> str:
    found = token()
    if found is None:
        msg = "You are not logged in to GitHub."
        raise CliError(msg, hint="Run `ctzd login --start`, show the person the code, then run `ctzd login --wait`.")
    return found


def profile() -> Profile | None:
    path = config.user_dir() / "profile.json"
    if not path.is_file():
        return None
    return Profile.model_validate_json(path.read_text(encoding="utf-8"))


def identity() -> tuple[str, str] | None:
    """The name and email git should record on commits."""
    current = profile()
    if current is None:
        return None
    return (current.name or current.login, current.email or f"{current.login}@users.noreply.{config.load().github.host}")


def start(*, client: httpx.Client, now: Callable[[], datetime], open_browser: bool = True) -> PendingLogin:
    github = config.load().github
    response = client.post(f"https://{github.host}/login/device/code", data={"client_id": github.oauth_client_id, "scope": _SCOPES}, headers={"Accept": "application/json"})
    response.raise_for_status()
    code = _DeviceCode.model_validate(response.json())
    pending = PendingLogin(
        device_code=code.device_code, user_code=code.user_code, verification_uri=code.verification_uri, interval=code.interval, expires_at=now() + timedelta(seconds=code.expires_in)
    )
    folder = config.user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pending-login.json").write_text(pending.model_dump_json(), encoding="utf-8")
    if open_browser:
        webbrowser.open(pending.verification_uri)
    return pending


def wait(*, client: httpx.Client, now: Callable[[], datetime], sleep: Callable[[float], None] = time.sleep) -> Profile:
    path = config.user_dir() / "pending-login.json"
    if not path.is_file():
        msg = "No login is in progress."
        raise CliError(msg, hint="Run `ctzd login --start` first.")
    pending = PendingLogin.model_validate_json(path.read_text(encoding="utf-8"))
    github = config.load().github
    interval = float(pending.interval)
    while now() < pending.expires_at:
        sleep(interval)
        response = client.post(
            f"https://{github.host}/login/oauth/access_token",
            data={"client_id": github.oauth_client_id, "device_code": pending.device_code, "grant_type": _DEVICE_GRANT},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        reply = _TokenReply.model_validate(response.json())
        if reply.access_token is not None:
            path.unlink()
            return _store(reply.access_token, client=client)
        if reply.error == "slow_down":
            interval = float(reply.interval or interval + 5)
        elif reply.error != "authorization_pending":
            path.unlink()
            msg = f"GitHub refused the login: {reply.error}."
            raise CliError(msg, hint="Run `ctzd login --start` again.")
    path.unlink()
    msg = "The login code expired before it was approved."
    raise CliError(msg, hint="Run `ctzd login --start` again.")


def fetch_profile(access_token: str, *, client: httpx.Client) -> Profile:
    response = client.get(f"{config.load().github.api_url}/user", headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    if response.status_code == httpx.codes.UNAUTHORIZED:
        msg = "GitHub rejected the stored token."
        raise CliError(msg, hint="Run `ctzd login --start` to log in again.")
    response.raise_for_status()
    return Profile.model_validate(response.json())


def _store(access_token: str, *, client: httpx.Client) -> Profile:
    current = fetch_profile(access_token, client=client)
    try:
        keyring.set_password(_KEYRING_SERVICE, config.load().github.host, access_token)
    except keyring.errors.KeyringError as err:
        msg = "The operating system's credential store is not available."
        raise CliError(msg, hint=f"Set {LOGIN_VARIABLE} instead.") from err
    folder = config.user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "profile.json").write_text(json.dumps(current.model_dump(mode="json")), encoding="utf-8")
    return current


def utc_now() -> datetime:
    return datetime.now(UTC)


def http_client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT)
