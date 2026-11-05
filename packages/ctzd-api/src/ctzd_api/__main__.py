"""Run the API: `python -m ctzd_api`. With CTZD_API_FAKE=1 it runs on in-memory fakes for local demos."""

import os

import uvicorn
from azure.identity import DefaultAzureCredential

from ctzd_api import fakes
from ctzd_api.app import create_app
from ctzd_api.clients import AirflowClient, GitHubClient, JenkinsClient, KeyVaultClient, KubeClient
from ctzd_api.service import Services
from ctzd_api.settings import ApiSettings
from ctzd_cli import config


def services_from_env() -> Services:
    platform = config.load()
    if os.environ.get("CTZD_API_FAKE", "") == "1":
        return Services(
            github=fakes.FakeGitHub(tokens={"demo-token": "demo"}, members={"demo"}),
            jenkins=fakes.FakeJenkins(),
            airflow=fakes.FakeAirflow(),
            kube=fakes.FakeKube(),
            secrets=fakes.FakeSecrets(),
            platform=platform,
        )
    settings = ApiSettings()
    return Services(
        github=GitHubClient(platform.github.api_url, platform.github.org, settings.github_token.get_secret_value()),
        jenkins=JenkinsClient(settings.jenkins_url, user=settings.jenkins_user, api_token=settings.jenkins_token.get_secret_value(), folder=settings.jenkins_folder),
        airflow=AirflowClient(platform.platform.airflow_url, username=settings.airflow_username, password=None if settings.airflow_password is None else settings.airflow_password.get_secret_value()),
        kube=KubeClient(settings.namespace),
        secrets=KeyVaultClient(settings.keyvault_url, DefaultAzureCredential()),
        platform=platform,
    )


def main() -> None:
    uvicorn.run(create_app(services_from_env()), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))  # noqa: S104 - the API serves the cluster network from inside its pod.


if __name__ == "__main__":
    main()
