"""Stage 2a: the Jenkins pipeline, dry run, on a real Jenkins.

    uv run python -m validation.stage2_jenkins [WORK_DIR]

A Jenkins LTS container runs `ctzdApp()` from this repository's library against
generated apps in local git repositories. The ctzd steps are real (`ctzd ci
check`, `render`, `release`, the job-spec publisher); docker, helm, and kubectl
are stubs that record each call (validation/jenkins). Builds are read through
the platform API's own `JenkinsClient`.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from ctzd_api.clients import JenkinsClient
from ctzd_api.gateways import JenkinsBuild
from ctzd_api.service import DELETE_JOB, ROLLBACK_JOB
from validation.harness import CTZD, REPO, Report, build_wheels, platform_config, tool

IMAGE = "ctzd-validation-jenkins"
CONTAINER = "ctzd-validation-jenkins"
PORT = 18080
URL = f"http://127.0.0.1:{PORT}"
FOLDER = "ctzd-apps"
BUILD_TIMEOUT = 1500.0
CASC = """jenkins:
  systemMessage: "ctzd stage 2 dry run"
  numExecutors: 3
  labelString: "ctzd"
  mode: NORMAL
  globalNodeProperties:
    - envVars:
        env:
          - {{key: CTZD_REGISTRY, value: "registry.local:5000"}}
          - {{key: CTZD_BASE_IMAGE, value: "registry.local:5000/ctzd/base:py3.14"}}
          - {{key: CTZD_CHART, value: "oci://registry.local:5000/helm/ctzd-app"}}
          - {{key: CTZD_DAGS_REPO, value: "https://github.contoso.com/ctzd-platform/ctzd-dags.git"}}
          - {{key: CTZD_PLATFORM_CONFIG, value: "{work}/platform.toml"}}
          - {{key: GIT_CONFIG_GLOBAL, value: "{work}/gitconfig"}}
          - {{key: UV_FIND_LINKS, value: "{work}/wheels"}}
          - {{key: CTZD_CALLS, value: "{work}/calls.log"}}
          - {{key: CTZD_PAUSED_DIR, value: "{work}/paused"}}
unclassified:
  globalLibraries:
    libraries:
      - name: ctzd
        defaultVersion: main
        retriever:
          modernSCM:
            scm:
              git:
                remote: "file://{work}/repos/ctzd-library.git"
credentials:
  system:
    domainCredentials:
      - credentials:
          - string: {{scope: GLOBAL, id: ctzd-github, secret: "jenkins-bot-token"}}
          - usernamePassword: {{scope: GLOBAL, id: ctzd-acr, username: acr, password: acr}}
jobs:
  - script: |
      folder('{folder}')
      ['fi-dry-jobs', 'fi-dry-page'].each {{ repo ->
        multibranchPipelineJob('{folder}/' + repo) {{
          branchSources {{ git {{ id(repo); remote('file://{work}/repos/' + repo + '.git') }} }}
        }}
      }}
      pipelineJob('{rollback}') {{
        parameters {{ stringParam('REPO'); stringParam('RELEASE') }}
        definition {{ cpsScm {{ scm {{ git {{ remote {{ url("file://{work}/repos/ctzd-library.git") }}; branch('main') }} }}; scriptPath('jobs/ctzd-rollback.Jenkinsfile') }} }}
      }}
      pipelineJob('{delete}') {{
        parameters {{ stringParam('REPO') }}
        definition {{ cpsScm {{ scm {{ git {{ remote {{ url("file://{work}/repos/ctzd-library.git") }}; branch('main') }} }}; scriptPath('jobs/ctzd-delete.Jenkinsfile') }} }}
      }}
"""


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run([tool("git"), "-c", "user.name=v", "-c", "user.email=v@contoso.com", "-c", "init.defaultBranch=main", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def ctzd_json(argv: list[str], cwd: Path, env: dict[str, str]) -> dict[str, object]:
    done = subprocess.run([str(CTZD), *argv, "--json"], cwd=cwd, env=env, capture_output=True, text=True, check=False)
    parsed: object = json.loads(done.stdout) if done.stdout.strip().startswith("{") else {}
    return {str(key): value for key, value in parsed.items()} if isinstance(parsed, dict) else {}


def publish(work: Path, source: Path, repo: str) -> Path:
    """`source` as the main branch of a bare repository under work/repos."""
    bare = work / "repos" / f"{repo}.git"
    git(work, "init", "--quiet", "--bare", str(bare))
    if not (source / ".git").is_dir():
        git(source, "init", "--quiet")
        git(source, "add", "--all")
        git(source, "commit", "--quiet", "-m", f"First version of {repo}")
    git(source, "remote", "add", "origin", str(bare))
    git(source, "push", "--quiet", "origin", "HEAD:refs/heads/main")
    return bare


def prepare(work: Path) -> None:
    wheels = build_wheels(work / "wheels")
    subprocess.run([tool("uv"), "build", "--quiet", "--package", "ctzd", "--out-dir", str(wheels)], cwd=REPO, check=True)
    cfg = platform_config(work, wheels, api_url="http://127.0.0.1:9")  # no API: ci check falls back to the bundled release
    (work / "gitconfig").write_text(f'[url "file://{work}/repos/"]\n\tinsteadOf = https://github.contoso.com/ctzd-apps/\n\tinsteadOf = https://github.contoso.com/ctzd-platform/\n')
    (work / "paused").mkdir()
    library = work / "library"
    for part in ("vars", "resources", "jobs"):
        shutil.copytree(REPO / "jenkins" / part, library / part)
    publish(work, library, "ctzd-library")
    dags = work / "dags"
    dags.mkdir()
    (dags / "README.md").write_text("specs written by Jenkins\n")
    publish(work, dags, "ctzd-dags")
    env = {"CTZD_PLATFORM_CONFIG": str(cfg), "CTZD_HOME": str(work / "home"), "PATH": "/usr/bin:/bin:" + str(Path(tool("uv")).parent)}
    for name, ui in (("dry-jobs", "none"), ("dry-page", "react")):
        created = ctzd_json(["new", name, "--sbu", "fi", "--ui", ui, "--offline", "--owner", "ann.lee@contoso.com", "--dir", str(work / "apps")], work, env)
        if created.get("ok") is not True:
            msg = f"could not create {name}: {created}"
            raise RuntimeError(msg)
        publish(work, work / "apps" / name, f"fi-{name}")


def start_jenkins(work: Path) -> None:
    docker = tool("docker")
    subprocess.run([docker, "rm", "-f", CONTAINER], capture_output=True, check=False)
    casc = work / "casc"
    casc.mkdir()
    (casc / "casc.yaml").write_text(CASC.format(work=work, folder=FOLDER, rollback=ROLLBACK_JOB, delete=DELETE_JOB))
    mounts = ["-v", f"{work}:{work}", "-v", f"{casc}:/casc:ro"]
    subprocess.run([docker, "run", "-d", "--name", CONTAINER, "-p", f"127.0.0.1:{PORT}:8080", *mounts, IMAGE], check=True, capture_output=True)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{URL}/job/{FOLDER}/api/json", timeout=5).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    msg = "Jenkins did not come up"
    raise RuntimeError(msg)


def scan(repo: str) -> None:
    response = httpx.post(f"{URL}/job/{FOLDER}/job/{repo}/build", params={"delay": "0"}, timeout=30)
    if response.is_error:  # Jenkins answers an accepted scan with a redirect to the job page
        response.raise_for_status()


def wait_for(client: JenkinsClient, repo: str, count: int, *, branch_url: str | None = None) -> JenkinsBuild | None:
    """The `count`th build of the app's main branch, once it has finished."""
    deadline = time.monotonic() + BUILD_TIMEOUT
    while time.monotonic() < deadline:
        if branch_url is None:
            builds = sorted(client.builds(repo), key=lambda build: build.number)
            if len(builds) >= count and not builds[count - 1].building and builds[count - 1].result is not None:
                return builds[count - 1]
        else:
            reply = httpx.get(f"{branch_url}lastBuild/api/json", timeout=10)
            if reply.is_success and reply.json().get("building") is False:
                return None
        time.sleep(5)
    return None


def calls(work: Path) -> str:
    path = work / "calls.log"
    return path.read_text() if path.is_file() else ""


def push_change(work: Path, name: str, branch: str = "main") -> None:
    app = work / "apps" / name
    (app / "README.md").write_text(f"{(app / 'README.md').read_text()}\nChanged on {branch} at {time.time()}.\n")
    git(app, "commit", "--quiet", "-am", f"Change on {branch}")
    git(app, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")


def check_jobs_app(report: Report, client: JenkinsClient, work: Path) -> None:
    scan("fi-dry-jobs")
    first = wait_for(client, "fi-dry-jobs", 1)
    tail = "" if first is None else client.console_tail(first, 40)
    report.expect("a jobs-only app builds, publishes its spec, and is released as v1", ok=first is not None and first.result == "SUCCESS" and first.description == "release v1", seen=(first, tail))
    report.expect("its image is built and pushed to the overridden registry", ok="docker push registry.local:5000/apps/fi-dry-jobs:" in calls(work), seen=calls(work)[-800:])
    report.expect("an app without a dashboard runs no helm", ok="helm upgrade --install fi-dry-jobs" not in calls(work))
    report.expect("the release is recorded in the state ConfigMap", ok="ctzd-state-fi-dry-jobs" in calls(work) and "release=1" in calls(work))
    spec_text = git(work / "dags", "--git-dir", str(work / "repos" / "ctzd-dags.git"), "show", "main:specs/fi/dry-jobs.json") if first is not None else ""
    report.expect("the job spec reaches ctzd-dags in the version-2 shape", ok='"version": 2' in spec_text and '"source_secrets"' in spec_text, seen=spec_text[:300])
    tags = git(work, "--git-dir", str(work / "repos" / "fi-dry-jobs.git"), "tag", "--list", "ctzd/v*")
    report.expect("the commit is tagged ctzd/v1", ok=tags.splitlines() == ["ctzd/v1"], seen=tags)
    (work / "paused" / "ctzd-state-fi-dry-jobs").write_text("true")
    push_change(work, "dry-jobs")
    scan("fi-dry-jobs")
    paused = wait_for(client, "fi-dry-jobs", 2)
    report.expect(
        "while paused, a push runs the checks and deploys nothing",
        ok=paused is not None and paused.result == "SUCCESS" and paused.description == "checks passed; auto-deploy is paused after a rollback",
        seen=(paused, None if paused is None else client.console_tail(paused, 30)),
    )
    report.expect("while paused, no new release is tagged", ok=git(work, "--git-dir", str(work / "repos" / "fi-dry-jobs.git"), "tag", "--list", "ctzd/v*").splitlines() == ["ctzd/v1"])
    (work / "paused" / "ctzd-state-fi-dry-jobs").unlink()
    push_change(work, "dry-jobs", "feature")
    scan("fi-dry-jobs")
    wait_for(client, "fi-dry-jobs", 0, branch_url=f"{URL}/job/{FOLDER}/job/fi-dry-jobs/job/feature/")
    branch = httpx.get(f"{URL}/job/{FOLDER}/job/fi-dry-jobs/job/feature/lastBuild/wfapi/describe", timeout=10).json()
    stages = {stage["name"]: stage["status"] for stage in branch.get("stages", [])}
    report.expect("a branch other than main runs the checks only", ok=stages.get("Checks") == "SUCCESS" and stages.get("Image") in {None, "NOT_EXECUTED"}, seen=stages)


def check_dashboard_app(report: Report, client: JenkinsClient, work: Path) -> None:
    scan("fi-dry-page")
    build = wait_for(client, "fi-dry-page", 1)
    report.expect("a dashboard is installed with helm --atomic and the rendered values", ok="helm upgrade --install fi-dry-page" in calls(work) and "--atomic" in calls(work), seen=calls(work)[-800:])
    report.expect(
        "a dashboard that fails the smoke test is rolled back and fails the build at that stage",
        ok=build is not None and build.result == "FAILURE" and build.failed_stage == "Smoke test" and "helm rollback fi-dry-page" in calls(work),
        seen=(build, None if build is None else client.console_tail(build, 30)),
    )
    listing = git(work, "--git-dir", str(work / "repos" / "ctzd-dags.git"), "ls-tree", "-r", "--name-only", "main")
    report.expect("a build that failed its smoke test publishes no job spec", ok="specs/fi/dry-page.json" not in listing, seen=listing)


def check_platform_jobs(report: Report, client: JenkinsClient, work: Path) -> None:
    client.trigger(ROLLBACK_JOB, {"REPO": "fi-dry-jobs", "RELEASE": "1"})
    rolled = wait_job(ROLLBACK_JOB)
    report.expect(
        "the rollback job restores a release and pauses auto-deploy", ok=rolled.get("result") == "SUCCESS" and "paused=true" in calls(work), seen=(rolled.get("result"), console(ROLLBACK_JOB))
    )
    client.trigger(DELETE_JOB, {"REPO": "fi-dry-jobs"})
    deleted = wait_job(DELETE_JOB)
    listing = git(work, "--git-dir", str(work / "repos" / "ctzd-dags.git"), "ls-tree", "-r", "--name-only", "main")
    report.expect(
        "the delete job removes the job spec and records the deletion for the janitor",
        ok=deleted.get("result") == "SUCCESS" and "specs/fi/dry-jobs.json" not in listing and "deleted/fi/dry-jobs.json" in listing,
        seen=(deleted.get("result"), listing, console(DELETE_JOB)),
    )


def wait_job(job: str) -> dict[str, object]:
    deadline = time.monotonic() + BUILD_TIMEOUT
    while time.monotonic() < deadline:
        reply = httpx.get(f"{URL}/job/{job}/lastBuild/api/json", timeout=10)
        if reply.is_success and reply.json().get("building") is False:
            body: object = reply.json()
            return {str(key): value for key, value in body.items()} if isinstance(body, dict) else {}
        time.sleep(5)
    return {}


def console(job: str) -> str:
    return httpx.get(f"{URL}/job/{job}/lastBuild/consoleText", timeout=10).text[-1500:]


def main() -> int:
    work = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="ctzd-stage2-"))
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    report = Report("stage 2a (Jenkins dry run)")
    prepare(work)
    start_jenkins(work)
    client = JenkinsClient(URL, user="ctzd", api_token="", folder=FOLDER)  # this Jenkins has no users; see validation/jenkins/Dockerfile
    try:
        check_jobs_app(report, client, work)
        check_dashboard_app(report, client, work)
        check_platform_jobs(report, client, work)
    finally:
        logs = subprocess.run([tool("docker"), "logs", CONTAINER], capture_output=True, text=True, check=False)
        (work / "jenkins.log").write_text(logs.stdout + logs.stderr)
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main())
