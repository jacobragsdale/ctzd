"""Files bundled with the CLI: the locked platform files, the starter app, and the skills."""

from importlib import metadata, resources
from importlib.resources.abc import Traversable

from ctzd_cli.appconfig import Ui


def version() -> str:
    return metadata.version("ctzd")


def _root() -> Traversable:
    return resources.files("ctzd_cli").joinpath("assets")


def _tree(folder: Traversable, prefix: str = "") -> dict[str, str]:
    files: dict[str, str] = {}
    if not folder.is_dir():
        return files
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        path = f"{prefix}{entry.name}"
        if entry.is_dir():
            if entry.name != "__pycache__":
                files |= _tree(entry, f"{path}/")
        else:
            files[path] = entry.read_text(encoding="utf-8")
    return files


def tree(relative: str) -> dict[str, str]:
    return _tree(_root().joinpath(relative))


def skills(sbu: str) -> dict[str, str]:
    """Skill files keyed by their path under `.agents/skills/`: the common skills plus the SBU's own."""
    return tree("skills/common") | tree(f"skills/sbu/{sbu}")


def locked_files(sbu: str) -> dict[str, str]:
    files = tree("locked")
    for path, text in skills(sbu).items():
        files[f".agents/skills/{path}"] = text
    return files


def _render(text: str, tokens: dict[str, str]) -> str:
    for token, value in tokens.items():
        text = text.replace("{{" + token + "}}", value)
    return text


def starter_files(ui: Ui, *, title: str, sbu_name: str) -> dict[str, str]:
    tokens = {"title": title, "sbu_name": sbu_name}
    files = tree("starter/common")
    if ui != "none":
        files |= tree(f"starter/{ui}")
    return {path: _render(text, tokens) for path, text in files.items()}


def job_files(job: str) -> dict[str, str]:
    return {path.replace("__job__", job): _render(text, {"job": job}) for path, text in tree("starter/job").items()}
