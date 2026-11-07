"""Views over a platform release: the files `ctzd` writes into an app, keyed by their path in the app."""

from importlib import metadata

from ctzd_cli import api_models as m
from ctzd_cli.appconfig import Ui

SKILLS_DIR = ".agents/skills"
CLAUDE_SKILLS_DIR = ".claude/skills"


def version() -> str:
    return metadata.version("ctzd")


def _folder(release: m.PlatformRelease, prefix: str) -> dict[str, str]:
    return {path.removeprefix(prefix): text for path, text in sorted(release.files.items()) if path.startswith(prefix)}


def locked_files(release: m.PlatformRelease) -> dict[str, str]:
    """The build files: committed in every app and drift-checked against the app's recorded release."""
    return _folder(release, "locked/")


def skills(release: m.PlatformRelease, sbu: str) -> dict[str, str]:
    """Skill files keyed by their path under `.agents/skills/`: the common skills plus the SBU's own."""
    return _folder(release, "skills/common/") | _folder(release, f"skills/sbu/{sbu}/")


def guidance_files(release: m.PlatformRelease, sbu: str) -> dict[str, str]:
    """`AGENTS.md` and the skills, plus the copy Claude Code needs because it does not read `.agents/skills/`."""
    files = _folder(release, "guidance/")
    for path, text in skills(release, sbu).items():
        files[f"{SKILLS_DIR}/{path}"] = text
        files[f"{CLAUDE_SKILLS_DIR}/{path}"] = text
    return files


def start_guide(release: m.PlatformRelease) -> str | None:
    """The guide `ctzd setup` hands the agent: logging in, starting an app, opening one."""
    return release.files.get("start/GUIDE.md")


def _render(text: str, tokens: dict[str, str]) -> str:
    for token, value in tokens.items():
        text = text.replace("{{" + token + "}}", value)
    return text


def starter_files(release: m.PlatformRelease, ui: Ui, *, title: str, sbu_name: str) -> dict[str, str]:
    tokens = {"title": title, "sbu_name": sbu_name}
    files = _folder(release, "starter/common/")
    if ui != "none":
        files |= _folder(release, f"starter/{ui}/")
    return {path: _render(text, tokens) for path, text in files.items()}


def job_files(release: m.PlatformRelease, job: str) -> dict[str, str]:
    return {path.replace("__job__", job): _render(text, {"job": job}) for path, text in _folder(release, "starter/job/").items()}
