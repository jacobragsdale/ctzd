"""Run the API: `python -m ctzd_api`. CTZD_API_FAKE runs some or all gateways on in-memory fakes, for local demos."""

import os
from pathlib import Path

import uvicorn
from azure.identity import DefaultAzureCredential
from pydantic import TypeAdapter

from ctzd_api import fakes
from ctzd_api.app import create_app
from ctzd_api.clients import AirflowClient, GitHubClient, GitHubReleases, JenkinsClient, KeyVaultClient, KubeClient
from ctzd_api.gateways import Airflow, GitHub, Jenkins, Kube, Releases, Secrets
from ctzd_api.releases import ReleaseStore
from ctzd_api.service import Services
from ctzd_api.settings import AirflowSettings, FakeSettings, GitHubSettings, JenkinsSettings, KeyVaultSettings, KubeSettings, ReleasesSettings
from ctzd_cli import config, releases
from ctzd_cli.config import PlatformConfig

_SECRETS: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])


def _github(fake: FakeSettings, platform: PlatformConfig) -> GitHub:
    if fake.fakes("github"):
        return fakes.FakeGitHub(tokens={"demo-token": "demo"}, members={"demo"})
    return GitHubClient(platform.github.api_url, platform.github.org, GitHubSettings().github_token.get_secret_value())


def _releases(fake: FakeSettings, platform: PlatformConfig) -> Releases:
    if fake.fakes("releases"):
        seed = releases.seed()
        channels = f"[channels]\npilot = {seed.release}\ngeneral = {seed.release}\n"
        return fakes.FakeReleases(channels=channels, archives={seed.release: fakes.archive(releases.seed_files())})
    settings = ReleasesSettings()
    return GitHubReleases(platform.github.api_url, settings.releases_repo, settings.github_token.get_secret_value())


def _jenkins(fake: FakeSettings) -> Jenkins:
    if fake.fakes("jenkins"):
        return fakes.FakeJenkins()
    settings = JenkinsSettings()
    return JenkinsClient(settings.jenkins_url, user=settings.jenkins_user, api_token=settings.jenkins_token.get_secret_value(), folder=settings.jenkins_folder)


def _airflow(fake: FakeSettings, platform: PlatformConfig) -> Airflow:
    if fake.fakes("airflow"):
        return fakes.FakeAirflow()
    settings = AirflowSettings()
    password = None if settings.airflow_password is None else settings.airflow_password.get_secret_value()
    return AirflowClient(platform.platform.airflow_url, username=settings.airflow_username, password=password)


def _kube(fake: FakeSettings) -> Kube:
    return fakes.FakeKube() if fake.fakes("kube") else KubeClient(KubeSettings().namespace)


def _secrets(fake: FakeSettings) -> Secrets:
    if fake.fakes("secrets"):
        values = {} if fake.fake_secrets_file is None else _SECRETS.validate_json(Path(fake.fake_secrets_file).read_bytes())
        return fakes.FakeSecrets(values=values)
    return KeyVaultClient(KeyVaultSettings().keyvault_url, DefaultAzureCredential())


def services_from_env() -> Services:
    platform = config.load()
    fake = FakeSettings()
    return Services(
        github=_github(fake, platform),
        jenkins=_jenkins(fake),
        airflow=_airflow(fake, platform),
        kube=_kube(fake),
        secrets=_secrets(fake),
        releases=ReleaseStore(_releases(fake, platform)),
        platform=platform,
    )


def main() -> None:
    uvicorn.run(create_app(services_from_env()), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))  # noqa: S104 - the API serves the cluster network from inside its pod.


if __name__ == "__main__":
    main()
