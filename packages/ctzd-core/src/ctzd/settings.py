"""Runtime configuration, read once from the environment the platform (or `ctzd dev`) provides."""

import functools
from typing import Literal

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from ctzd._errors import CtzdError


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CTZD_", extra="ignore", frozen=True)

    app: str
    sbu: str
    env: Literal["local", "cluster"]
    store_url: str
    # Locally, tables the app has not written yet are read from the production store with a read-only SAS.
    shared_store_url: str | None = None
    shared_store_sas: SecretStr | None = None


class MailSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CTZD_SMTP_", extra="ignore", frozen=True)

    host: str
    port: int
    sender: str


def _missing(err: ValidationError, prefix: str) -> str:
    return ", ".join(sorted({prefix + str(error["loc"][0]).upper() for error in err.errors()}))


@functools.cache
def app_settings() -> AppSettings:
    try:
        return AppSettings()
    except ValidationError as err:
        msg = f"This code needs the app's settings ({_missing(err, 'CTZD_')}): run it through `ctzd dev`, `ctzd run JOB --local`, or `ctzd exec`."
        raise CtzdError(msg) from None


@functools.cache
def mail_settings() -> MailSettings:
    try:
        return MailSettings()
    except ValidationError as err:
        msg = f"Email needs the platform's mail settings ({_missing(err, 'CTZD_SMTP_')}), which only the cluster has; locally, ctzd saves messages instead."
        raise CtzdError(msg) from None


def session_name() -> str:
    """How a database session names the app (`ctzd:fi-bond-prices`), so a DBA can tell whose query it is; `ctzd` outside an app."""
    try:
        settings = app_settings()
    except CtzdError:
        return "ctzd"
    return f"ctzd:{settings.sbu}-{settings.app}"
