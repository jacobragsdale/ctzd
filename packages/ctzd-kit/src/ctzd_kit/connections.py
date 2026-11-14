"""How a driver connects to a source: the source's secret, which the platform delivers as one JSON object in
`CTZD_SOURCE_<NAME>`. Locally `ctzd env` writes it into `.env` from the dev Key Vault; in a pod External Secrets
Operator syncs it from the environment's Key Vault. Its fields are the platform team's to fill."""

import os
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, TypeAdapter, ValidationError

from ctzd_kit.errors import SourceError

ENV_PREFIX = "CTZD_SOURCE_"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class OracleSource(_Model):
    kind: Literal["oracle"]
    dsn: str = Field(min_length=1, description="Easy Connect string: host:port/service_name")
    user: str = Field(min_length=1)
    password: SecretStr


class MssqlSource(_Model):
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


class RestSource(_Model):
    kind: Literal["rest"]
    base_url: HttpUrl
    auth: RestAuth
    timeout_seconds: float = Field(default=30.0, gt=0)


class FileSource(_Model):
    """A folder of CSV, Excel or Parquet files: a local or network path, or a blob container,
    `abfs://container@account.dfs.core.windows.net/prefix`, read with a read-only SAS token."""

    kind: Literal["file"]
    url: str = Field(min_length=1)
    sas_token: SecretStr | None = None


type Connection = OracleSource | MssqlSource | RestSource | FileSource
_CONNECTION: TypeAdapter[Connection] = TypeAdapter(Annotated[OracleSource | MssqlSource | RestSource | FileSource, Field(discriminator="kind")])


def variable(name: str) -> str:
    return ENV_PREFIX + name.upper()


def connection(name: str) -> Connection:
    """Source `name`'s connection, from its secret."""
    raw = os.environ.get(variable(name))
    if raw is None:
        msg = f"Source {name!r} has no credentials here: {variable(name)} is not set. On a desktop, run `ctzd env`; in a pod, ask the platform team to add the source's secret."
        raise SourceError(msg)
    try:
        return _CONNECTION.validate_json(raw)
    except ValidationError as err:
        # include_input=False keeps passwords and tokens out of the message.
        problems = "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in err.errors(include_input=False, include_url=False))
        msg = f"{variable(name)} is not a valid connection: {problems}. Ask the platform team to fix the source's secret."
        raise SourceError(msg) from None
