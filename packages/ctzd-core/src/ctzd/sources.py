"""Named data sources. Each source is one environment variable holding a JSON description.

The platform syncs one Key Vault secret per source into every app pod as
`CTZD_SOURCE_<NAME>`; `ctzd env` writes the same variables into a local `.env`.
"""

import re
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, TypeAdapter, ValidationError

from ctzd._errors import SourceError

ENV_PREFIX = "CTZD_SOURCE_"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class _Described(_Model):
    """What `ctzd sources` tells an agent about a source, next to its kind; the platform team writes both."""

    description: str | None = Field(default=None, description="what the source holds, in business words")
    refreshed: str | None = Field(default=None, description="when its data is current, such as 'each business day by 07:00 London'")


class OracleSource(_Described):
    kind: Literal["oracle"]
    dsn: str = Field(min_length=1, description="Easy Connect string: host:port/service_name")
    user: str = Field(min_length=1)
    password: SecretStr


class MssqlSource(_Described):
    kind: Literal["mssql"]
    server: str = Field(min_length=1, description="host or host,port")
    database: str = Field(min_length=1)
    user: str = Field(min_length=1)
    password: SecretStr
    encrypt: bool = True
    trust_server_certificate: bool = False


class NoAuth(_Model):
    type: Literal["none"]


class BearerAuth(_Model):
    type: Literal["bearer"]
    token: SecretStr


class HeaderAuth(_Model):
    type: Literal["header"]
    name: str = Field(min_length=1)
    value: SecretStr


class BasicAuth(_Model):
    type: Literal["basic"]
    username: str = Field(min_length=1)
    password: SecretStr


class ClientCredentialsAuth(_Model):
    type: Literal["client_credentials"]
    token_url: HttpUrl
    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    scope: str | None = None


RestAuth = Annotated[NoAuth | BearerAuth | HeaderAuth | BasicAuth | ClientCredentialsAuth, Field(discriminator="type")]


class RestSource(_Described):
    kind: Literal["rest"]
    base_url: HttpUrl
    auth: RestAuth
    timeout_seconds: float = Field(default=30.0, gt=0)


class TeamsSource(_Described):
    kind: Literal["teams"]
    webhook_url: SecretStr


Source = Annotated[OracleSource | MssqlSource | RestSource | TeamsSource, Field(discriminator="kind")]
_SOURCE: TypeAdapter[OracleSource | MssqlSource | RestSource | TeamsSource] = TypeAdapter(Source)


def check_name(name: str) -> str:
    if _NAME.fullmatch(name) is None:
        msg = f"Invalid source name {name!r}: use lowercase letters, digits and underscores, starting with a letter."
        raise SourceError(msg)
    return name


def env_var(name: str) -> str:
    return ENV_PREFIX + check_name(name).upper()


def load_source(name: str, environ: Mapping[str, str]) -> OracleSource | MssqlSource | RestSource | TeamsSource:
    variable = env_var(name)
    raw = environ.get(variable)
    if raw is None:
        msg = (
            f"Source {name!r} is not configured: {variable} is not set. "
            "Locally, add the source to [sources].use in app.toml and run `ctzd env`. "
            "In the cluster, ask the platform team to add the source."
        )
        raise SourceError(msg)
    try:
        return _SOURCE.validate_json(raw)
    except ValidationError as err:
        # include_input=False keeps passwords and tokens out of the message.
        problems = "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in err.errors(include_input=False, include_url=False))
        msg = f"{variable} is not a valid source description: {problems}"
        raise SourceError(msg) from None
