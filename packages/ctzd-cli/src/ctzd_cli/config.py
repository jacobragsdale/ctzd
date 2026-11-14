"""Platform settings: where the platform's services are, and its departments. The bundled `platform.toml`, or the file
named by CTZD_PLATFORM_CONFIG."""

import functools
import os
import tomllib
from importlib import resources
from pathlib import Path
from typing import Literal

from packaging.requirements import InvalidRequirement, Requirement
from pydantic import BaseModel, ConfigDict, field_validator

from ctzd_cli.errors import CliError

_CONFIG = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)
Environment = Literal["dev", "prod"]


class _Model(BaseModel):
    model_config = _CONFIG


class GitHubConfig(_Model):
    host: str
    api_url: str
    org: str
    oauth_client_id: str


class EnvironmentConfig(_Model):
    """One cluster and what lives beside it."""

    apps_domain: str
    airflow_url: str
    key_vault: str
    store_url: str


class PlatformUrls(_Model):
    registry: str
    base_image: str
    mingit_url: str


class PackageIndex(_Model):
    name: str
    url: str
    default: bool = False
    format: str | None = None


class BuildOverrides(_Model):
    """For the platform's own tests only (scripts/e2e.sh, validation/): a package index that replaces the internal one in
    every app this CLI writes, such as a folder of locally built wheels. The bundled platform.toml has none."""

    python_index: tuple[PackageIndex, ...] | None = None


class SizeConfig(_Model):
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str


class SbuConfig(_Model):
    """A department: its kit, which its apps depend on, and the GitHub team whose members may create apps on it."""

    name: str
    kit: str
    github_team: str

    @field_validator("kit")
    @classmethod
    def _requirement(cls, value: str) -> str:
        try:
            Requirement(value)
        except InvalidRequirement:
            msg = f"{value!r} is not a package requirement such as 'ctzd-fi>=0.1'"
            raise ValueError(msg) from None
        return value

    @property
    def kit_package(self) -> str:
        """The kit's distribution name, such as `ctzd-fi`."""
        return Requirement(self.kit).name

    @property
    def kit_module(self) -> str:
        """The kit's import name, such as `ctzd_fi`."""
        return self.kit_package.replace("-", "_")


class PlatformConfig(_Model):
    version: int
    github: GitHubConfig
    platform: PlatformUrls
    environments: dict[Environment, EnvironmentConfig]
    build_overrides: BuildOverrides | None = None
    sizes: dict[str, SizeConfig]
    sbus: dict[str, SbuConfig]

    def sbu(self, key: str) -> SbuConfig:
        found = self.sbus.get(key)
        if found is None:
            msg = f"Unknown department {key!r}."
            raise CliError(msg, hint=f"Known departments: {', '.join(sorted(self.sbus))}.")
        return found

    def app_url(self, sbu: str, name: str, env: Environment = "prod") -> str:
        return f"https://{self.repo_name(sbu, name)}.{self.environments[env].apps_domain}"

    def dag_url(self, sbu: str, name: str, job: str, env: Environment = "prod") -> str:
        return f"{self.environments[env].airflow_url.rstrip('/')}/dags/{dag_id(sbu, name, job)}"

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
