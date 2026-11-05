"""Runtime configuration, read once from the environment the platform (or `ctzd dev`) provides."""

import functools
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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


@functools.cache
def app_settings() -> AppSettings:
    return AppSettings()


@functools.cache
def mail_settings() -> MailSettings:
    return MailSettings()
