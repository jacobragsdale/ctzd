"""Settings for each real gateway, read when that gateway starts; a faked gateway needs none."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIG = SettingsConfigDict(env_prefix="CTZD_API_", extra="ignore", frozen=True)


class GitHubSettings(BaseSettings):
    model_config = _CONFIG
    github_token: SecretStr


class ReleasesSettings(BaseSettings):
    model_config = _CONFIG
    github_token: SecretStr
    # The platform repo, `owner/name`, whose `platform-release/<n>` tags and channels.toml the API serves.
    releases_repo: str


class JenkinsSettings(BaseSettings):
    model_config = _CONFIG
    jenkins_url: str
    jenkins_user: str
    jenkins_token: SecretStr
    jenkins_folder: str


class AirflowSettings(BaseSettings):
    model_config = _CONFIG
    # Absent when Airflow runs the simple auth manager in all-admins mode.
    airflow_username: str | None = None
    airflow_password: SecretStr | None = None


class KubeSettings(BaseSettings):
    model_config = _CONFIG
    namespace: str


class KeyVaultSettings(BaseSettings):
    model_config = _CONFIG
    keyvault_url: str


class FakeSettings(BaseSettings):
    """CTZD_API_FAKE: `1` fakes every gateway; or a comma list such as `secrets,releases`."""

    model_config = _CONFIG
    fake: str = ""
    # JSON object of secret name -> value for a faked Key Vault, such as ctzd-source-fi-pricing.
    fake_secrets_file: str | None = None

    def fakes(self, name: str) -> bool:
        chosen = {part.strip() for part in self.fake.split(",") if part.strip()}
        return "1" in chosen or name in chosen
