from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CTZD_API_", extra="ignore", frozen=True)

    github_token: SecretStr
    jenkins_url: str
    jenkins_user: str
    jenkins_token: SecretStr
    jenkins_folder: str
    namespace: str
    keyvault_url: str
    # Absent when Airflow runs the simple auth manager in all-admins mode.
    airflow_username: str | None = None
    airflow_password: SecretStr | None = None
