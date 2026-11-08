"""Platform settings: where the platform's services are. The bundled `platform.toml`, or the file named by CTZD_PLATFORM_CONFIG."""

import functools
import os
import tomllib
from importlib import resources
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ctzd_cli.errors import CliError

_CONFIG = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class _Model(BaseModel):
    model_config = _CONFIG


class GitHubConfig(_Model):
    host: str
    api_url: str
    org: str
    oauth_client_id: str


class PlatformUrls(_Model):
    api_url: str
    apps_domain: str
    airflow_url: str
    registry: str
    base_image: str
    store_url: str
    mingit_url: str
    # Node's Windows zip, which `ctzd setup` unpacks into ctzd's own folder; without it, setup installs the Node wheel.
    node_url: str | None = None


class PackageIndex(_Model):
    name: str
    url: str
    default: bool = False
    format: str | None = None


class PythonConfig(_Model):
    """The package index apps install from. Tool pins and the Python range ship in platform releases."""

    index: tuple[PackageIndex, ...] = Field(min_length=1)
    sources: dict[str, dict[str, str]] = Field(default_factory=dict)


class NpmConfig(_Model):
    """Where an app's pages get the firm's npm packages, by name: a version range, or a `file:` path that may start
    with `~`. SHIM (MVP): basis-ui's packages are tarballs it packs from its main branch, until the internal registry
    serves them."""

    packages: dict[str, str] = Field(default_factory=dict)
    # The internal npm registry, which `ctzd setup` makes the user's default and installs pnpm from.
    registry: str | None = None

    def spec(self, name: str) -> str:
        found = self.packages.get(name)
        if found is None:
            msg = f"The platform config names no source for the npm package {name!r}."
            raise CliError(msg, hint="Add it to [npm.packages] in the platform config.")
        if found.startswith("file:~"):
            return "file:" + str(Path(found.removeprefix("file:")).expanduser())
        return found


class SizeConfig(_Model):
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str


class SbuConfig(_Model):
    name: str
    library: str
    github_team: str

    @field_validator("library")
    @classmethod
    def _requirement(cls, value: str) -> str:
        try:
            Requirement(value)
        except InvalidRequirement:
            msg = f"{value!r} is not a package requirement such as 'ctzd-fi>=0.1'"
            raise ValueError(msg) from None
        return value


class PlatformConfig(_Model):
    version: int
    github: GitHubConfig
    platform: PlatformUrls
    python: PythonConfig
    npm: NpmConfig = Field(default_factory=NpmConfig)
    sizes: dict[str, SizeConfig]
    sbus: dict[str, SbuConfig]

    def sbu(self, key: str) -> SbuConfig:
        config = self.sbus.get(key)
        if config is None:
            msg = f"Unknown business unit {key!r}."
            raise CliError(msg, hint=f"Known business units: {', '.join(sorted(self.sbus))}.")
        return config

    def app_url(self, sbu: str, name: str) -> str:
        return f"https://{sbu}-{name}.{self.platform.apps_domain}"

    def dag_url(self, sbu: str, name: str, job: str) -> str:
        return f"{self.platform.airflow_url.rstrip('/')}/dags/{dag_id(sbu, name, job)}"

    def repo_name(self, sbu: str, name: str) -> str:
        return f"{sbu}-{name}"


def dag_id(sbu: str, name: str, job: str) -> str:
    return f"{sbu}__{name.replace('-', '_')}__{job}"


def source_secret(source: str) -> str:
    """The Key Vault secret, and the Kubernetes secret synced from it, that holds one source."""
    return "ctzd-source-" + source.replace("_", "-")


def parse(text: str) -> PlatformConfig:
    return PlatformConfig.model_validate(tomllib.loads(text))


@functools.cache
def load() -> PlatformConfig:
    override = os.environ.get("CTZD_PLATFORM_CONFIG", "")
    text = Path(override).read_text(encoding="utf-8") if override else resources.files("ctzd_cli").joinpath("platform.toml").read_text(encoding="utf-8")
    return parse(text)


def user_dir() -> Path:
    """Where ctzd keeps its own state: the device-login handshake, the GitHub profile, MinGit."""
    override = os.environ.get("CTZD_HOME", "")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ctzd"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "ctzd"
