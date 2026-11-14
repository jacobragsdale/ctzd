"""`ctzd kit`: what a kit maintainer runs, in the platform repository (or the firm's fork of it).

- `kit new SBU --title TITLE` starts `kits/SBU`, a kit with no source but `sample`;
- `kit add source NAME --kind KIND` adds a source folder with a working loader, sample rows and a test for that kind,
  and rewrites the kit's `__init__.py`, which gives apps each source by its name;
- `kit check [PATH]` proves the kit meets the contract (PLAN.md section 7): its shape, that it installs from wheels on
  Windows and Linux beside `ctzd-common[dash]`, this repository's lint, type and test rules with the network blocked,
  the contract's own checks (`ctzd_kit.contract`), and an app of each template made on it and checked.
"""

import json
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from ctzd_cli import assets, checks, proc
from ctzd_cli.errors import CliError, UsageError

SOURCE_KINDS = ("oracle", "mssql", "rest", "file", "package")
_SBU = re.compile(r"[a-z][a-z0-9]{1,15}")
_SOURCE = re.compile(r"[a-z][a-z0-9_]{0,62}")
_TIMEOUT = 1800.0
PLATFORMS = ("x86_64-pc-windows-msvc", "x86_64-manylinux_2_28")
"""Where a kit must install from wheels alone: the desktop's Windows and the image's Linux."""


@dataclass(frozen=True)
class KitDir:
    root: Path
    module: str
    sbu: str
    title: str

    @property
    def package(self) -> Path:
        return self.root / "src" / self.module


def _uv() -> str:
    return proc.which("uv", hint="Install uv first.")


def _tokens(module: str, sbu: str, title: str, source: str = "") -> dict[str, str]:
    return {"kit": module, "kit_package": module.replace("_", "-"), "sbu": sbu, "title": title, "source": source}


def _write(root: Path, files: dict[str, str]) -> list[str]:
    written: list[str] = []
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
        written.append(path)
    return sorted(written)


def find_kit(start: Path) -> KitDir:
    """The kit folder at or above `start`: the one whose `src/*/kit.toml` names its department."""
    for folder in (start, *start.parents):
        found = sorted(folder.glob("src/*/kit.toml"))
        if found:
            spec = tomllib.loads(found[0].read_text(encoding="utf-8"))
            return KitDir(folder, found[0].parent.name, str(spec.get("sbu", "")), str(spec.get("title", "")))
    msg = f"{start} is not inside a kit."
    raise UsageError(msg, hint="Run it in kits/SBU, or pass the kit's folder; `ctzd kit new SBU --title TITLE` starts one.")


def init_module(kit: KitDir) -> str:
    """The kit's `__init__.py`: what its apps import, written from its source folders."""
    sources = sorted(entry.name for entry in (kit.package / "sources").iterdir() if entry.is_dir() and (entry / "loaders.py").is_file()) if (kit.package / "sources").is_dir() else []
    lines = [
        f'"""{kit.title}\'s kit: what its apps import. `ctzd kit add source` writes this file from the source folders."""',
        "",
        *sorted(
            [*(f"from {kit.module}.sources.{name} import loaders as {name}" for name in sources), "from ctzd_kit import app_query as query", "from ctzd_kit import cached, sample, store"],
            key=lambda line: line.split()[1],
        ),
        "",
        "__all__ = [" + ", ".join(f'"{name}"' for name in sorted([*sources, "cached", "query", "sample", "store"])) + "]",
        "",
    ]
    return "\n".join(lines)


def _add_to_workspace(workspace: Path, package: str) -> bool:
    """List `package` in the repository's dev group and [tool.uv.sources], so its checks import it; whether it changed."""
    import tomlkit  # noqa: PLC0415 - only kit new edits the repository's pyproject.toml
    from tomlkit.items import Array, Table  # noqa: PLC0415

    path = workspace / "pyproject.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    groups = document.get("dependency-groups")
    sources = document.get("tool", {}).get("uv", {}).get("sources")
    dev = groups.get("dev") if isinstance(groups, Table) else None
    if not isinstance(dev, Array) or not isinstance(sources, Table) or package in _root_dev(workspace):
        return False
    dev.append(package)
    sources[package] = tomlkit.inline_table()
    sources[package]["workspace"] = True
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
    return True


def new(directory: Path, *, sbu: str, title: str) -> tuple[KitDir, list[str]]:
    if _SBU.fullmatch(sbu) is None:
        msg = f"{sbu!r} is not a department code: 2 to 16 lowercase letters or digits, starting with a letter, such as fi."
        raise UsageError(msg)
    root = directory / sbu
    if root.exists():
        msg = f"{root} already exists."
        raise UsageError(msg, hint="Add a source to it with `ctzd kit add source NAME --kind KIND`.")
    module = f"ctzd_{sbu}"
    kit = KitDir(root, module, sbu, title)
    written = _write(root, assets.kit_scaffold("new", _tokens(module, sbu, title)))
    (kit.package / "__init__.py").write_text(init_module(kit), encoding="utf-8")
    written.append(f"src/{module}/__init__.py")
    try:
        workspace = _workspace(directory)
    except CliError:
        return kit, sorted(written)
    if _add_to_workspace(workspace, f"ctzd-{sbu}"):
        written.append(str(workspace / "pyproject.toml"))
    return kit, sorted(written)


def add_source(kit: KitDir, *, name: str, kind: str) -> list[str]:
    if _SOURCE.fullmatch(name) is None or name == "sample":
        msg = f"{name!r} is not a source name: lowercase letters, digits and underscores, starting with a letter, and not 'sample'."
        raise UsageError(msg)
    if kind not in SOURCE_KINDS:
        msg = f"{kind!r} is not a kind of source: give one of {', '.join(SOURCE_KINDS)}."
        raise UsageError(msg)
    folder = kit.package / "sources" / name
    if folder.exists():
        msg = f"{kit.module} already has a source {name}."
        raise UsageError(msg)
    tokens = _tokens(kit.module, kit.sbu, kit.title, name)
    common = assets.kit_scaffold("source/common", tokens)
    test = common.pop("test.py")
    written = _write(folder, common | assets.kit_scaffold(f"source/{kind}", tokens))
    (kit.root / "tests" / f"test_{name}.py").write_text(test, encoding="utf-8")
    (kit.package / "__init__.py").write_text(init_module(kit), encoding="utf-8")
    return sorted([*(f"src/{kit.module}/sources/{name}/{path}" for path in written), f"tests/test_{name}.py", f"src/{kit.module}/__init__.py"])


# ---------------------------------------------------------------- the contract


def _workspace(start: Path) -> Path:
    """The platform repository: the folder at or above `start` whose pyproject.toml is a uv workspace."""
    for folder in (start, *start.parents):
        path = folder / "pyproject.toml"
        if path.is_file() and "workspace" in tomllib.loads(path.read_text(encoding="utf-8")).get("tool", {}).get("uv", {}):
            return folder
    msg = "The kit is not inside the platform repository's uv workspace."
    raise CliError(msg, hint="Keep kits in kits/SBU of the platform repository.")


def _members(workspace: Path) -> dict[str, Path]:
    """Each package of the workspace, by its distribution name."""
    found: dict[str, Path] = {}
    for path in [*workspace.glob("packages/*/pyproject.toml"), *workspace.glob("kits/*/pyproject.toml"), *workspace.glob("kits/*/libs/*/pyproject.toml")]:
        name = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {}).get("name")
        if isinstance(name, str):
            found[name] = path.parent
    return found


def _dependencies(project: Path) -> list[Requirement]:
    found: list[Requirement] = []
    for text in tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8")).get("project", {}).get("dependencies", []):
        try:
            found.append(Requirement(str(text)))
        except InvalidRequirement:
            continue
    return found


def local_packages(workspace: Path, kit: KitDir) -> list[str]:
    """The workspace's packages an app on `kit` installs: the kit, ctzd-common, and what each of them needs from the
    workspace, ctzd-kit and the department's own packages among them."""
    members = _members(workspace)
    wanted = [kit.module.replace("_", "-"), "ctzd-common"]
    seen: list[str] = []
    while wanted:
        name = wanted.pop()
        if name in seen or name not in members:
            continue
        seen.append(name)
        wanted += [requirement.name for requirement in _dependencies(members[name])]
    return sorted(seen)


def shape(kit: KitDir, workspace: Path) -> list[str]:
    """Contract item 1: the package is `ctzd-<sbu>`, builds on ctzd-kit, and the platform can find it."""
    problems: list[str] = []
    project = tomllib.loads((kit.root / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    expected = f"ctzd-{kit.sbu}"
    if project.get("name") != expected:
        problems.append(f"pyproject.toml names the package {project.get('name')!r}; a kit is {expected!r}, after its kit.toml's sbu.")
    if not any(requirement.name == "ctzd-kit" for requirement in _dependencies(kit.root)):
        problems.append("pyproject.toml does not depend on ctzd-kit, which every kit builds on.")
    entry = project.get("entry-points", {}).get("ctzd.kits", {})
    if entry != {kit.sbu: kit.module}:
        problems.append(f'pyproject.toml needs [project.entry-points."ctzd.kits"] {kit.sbu} = "{kit.module}", by which an app finds its kit.')
    if not (kit.package / "__init__.py").is_file() or (kit.package / "__init__.py").read_text(encoding="utf-8") != init_module(kit):
        problems.append(f"src/{kit.module}/__init__.py is not what the source folders give: run `ctzd kit add source` for a new source, or put the file back.")
    for folder in sorted((kit.package / "sources").iterdir()) if (kit.package / "sources").is_dir() else []:
        if folder.is_dir() and not folder.name.startswith(("_", ".")):
            missing = [part for part in ("source.toml", "loaders.py", "fixtures") if not (folder / part).exists()]
            problems += [f"sources/{folder.name} has no {', '.join(missing)}: every source folder holds source.toml, loaders.py and fixtures/."] if missing else []
            description = (folder / "source.toml").read_text(encoding="utf-8") if (folder / "source.toml").is_file() else ""
            if "Describe what " in description or "Say when its data" in description:
                problems.append(f"sources/{folder.name}/source.toml still has the scaffold's words: say what it holds and when it is current.")
    if kit.module.replace("_", "-") not in _root_dev(workspace):
        problems.append(f"The repository's pyproject.toml does not list {expected} in its dev group and [tool.uv.sources], so its checks cannot import it.")
    return problems


def _root_dev(workspace: Path) -> set[str]:
    document = tomllib.loads((workspace / "pyproject.toml").read_text(encoding="utf-8"))
    dev = document.get("dependency-groups", {}).get("dev", [])
    return {Requirement(str(text)).name for text in dev if isinstance(text, str)}


def build_wheels(workspace: Path, packages: list[str], target: Path) -> proc.Completed | None:
    target.mkdir(parents=True, exist_ok=True)
    for package in packages:
        built = proc.run([_uv(), "build", "--quiet", "--package", package, "--out-dir", str(target)], cwd=workspace, timeout=_TIMEOUT)
        if not built.ok:
            return built
    return None


def wheels(workspace: Path, kit: KitDir, wheelhouse: Path) -> list[str]:
    """Contract item 2: the kit installs from wheels alone, beside ctzd-common[dash], on Python 3.14 on the desktop's
    Windows and the image's Linux. Resolved, not installed."""
    requirements = wheelhouse / "requirements.in"
    requirements.write_text(f"{kit.module.replace('_', '-')}\nctzd-common[dash]\n", encoding="utf-8")
    problems: list[str] = []
    for platform in PLATFORMS:
        resolved = proc.run(
            [_uv(), "pip", "compile", "--quiet", "--python-version", "3.14", "--python-platform", platform, "--only-binary", ":all:", "--find-links", str(wheelhouse), str(requirements)],
            cwd=workspace,
            timeout=_TIMEOUT,
        )
        if not resolved.ok:
            problems.append(f"The kit does not install from wheels alone on {platform}: {proc.tail(resolved.output, 8)}")
    return problems


def rules(workspace: Path, kit: KitDir) -> list[str]:
    """Contract items 1 and 5: this repository's lint, type and test rules, the tests with the network blocked."""
    relative = kit.root.relative_to(workspace).as_posix()
    problems: list[str] = []
    for name, args in (
        ("ruff format", ["ruff", "format", "--check", relative]),
        ("ruff", ["ruff", "check", relative]),
        ("ty", ["ty", "check", relative]),
        ("tests", ["pytest", "-q", "-p", "no:randomly", "-p", "ctzd_kit.pytest_nonetwork", f"{relative}/tests"]),
    ):
        result = proc.run([_uv(), "run", "--no-sync", *args], cwd=workspace, timeout=_TIMEOUT)
        if not result.ok:
            problems.append(f"{name} failed: {proc.tail(result.output, 30)}")
    return problems


def contract(workspace: Path, kit: KitDir) -> tuple[list[str], list[str]]:
    """Contract items 3 to 7, from inside the kit's environment (`ctzd_kit.contract`)."""
    result = proc.run([_uv(), "run", "--no-sync", "python", "-m", "ctzd_kit.contract", kit.module], cwd=workspace, timeout=_TIMEOUT)
    last = result.output.strip().splitlines()[-1] if result.output.strip() else ""
    try:
        found: object = json.loads(last)
    except ValueError:
        found = None
    if not isinstance(found, dict):
        return [f"The contract's checks did not run: {proc.tail(result.output, 20)}"], []
    problems, warnings = found.get("problems", []), found.get("warnings", [])
    return [str(item) for item in problems] if isinstance(problems, list) else [], [str(item) for item in warnings] if isinstance(warnings, list) else []


def apps(kit: KitDir, wheelhouse: Path, scratch: Path) -> list[str]:
    """An app of each template, made offline on the kit from its wheels, and `ctzd check` run in it."""
    config = scratch / "platform.toml"
    bundled = (Path(__file__).parent / "platform.toml").read_text(encoding="utf-8")
    index = f'python_index = [{{name = "local", url = "{wheelhouse}", format = "flat"}}, {{name = "pypi", url = "https://pypi.org/simple", default = true}}]'
    config.write_text(f"{bundled}\n[build_overrides]\n{index}\n", encoding="utf-8")
    env = {"CTZD_PLATFORM_CONFIG": str(config), "CTZD_HOME": str(scratch / "home")}
    ctzd = shutil.which("ctzd") or "ctzd"
    problems: list[str] = []
    for kind in ("dash", "job"):
        name = f"check-{kit.sbu}-{kind}"
        made = proc.run([ctzd, "new", name, "--sbu", kit.sbu, "--kind", kind, "--offline", "--owner", "kit-check@contoso.com", "--dir", str(scratch), "--json"], cwd=scratch, env=env, timeout=_TIMEOUT)
        if not made.ok:
            problems.append(f"`ctzd new --kind {kind}` on the kit failed: {proc.tail(made.output, 20)}")
            continue
        checked = proc.run([ctzd, "check", "--json"], cwd=scratch / name, env=env, timeout=_TIMEOUT)
        if not checked.ok:
            problems.append(f"`ctzd check` on a {kind} app made on the kit failed: {proc.tail(checked.output, 40)}")
    return problems


@dataclass(frozen=True)
class Checked:
    steps: list[checks.Step]
    warnings: list[str]


def check(start: Path, *, make_apps: bool) -> Checked:
    kit = find_kit(start)
    workspace = _workspace(kit.root)
    steps: list[checks.Step] = []
    warnings: list[str] = []

    def step(name: str, problems: list[str]) -> None:
        steps.append(checks.Step(name, "failed", "\n".join(problems)) if problems else checks.Step(name, "passed"))

    step("shape", shape(kit, workspace))
    step("rules", rules(workspace, kit))
    problems, warned = contract(workspace, kit)
    step("contract", problems)
    warnings += warned
    scratch = Path(tempfile.mkdtemp(prefix=f"ctzd-kit-check-{kit.sbu}-", dir=Path.home() / ".cache"))
    try:
        wheelhouse = scratch / "wheels"
        failed = build_wheels(workspace, local_packages(workspace, kit), wheelhouse)
        if failed is not None:
            step("wheels", [f"A package of the workspace does not build: {proc.tail(failed.output, 20)}"])
            return Checked(steps, warnings)
        step("wheels", wheels(workspace, kit, wheelhouse))
        if make_apps:
            step("apps", apps(kit, wheelhouse, scratch))
        else:
            steps.append(checks.Step("apps", "skipped", "--no-apps"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return Checked(steps, warnings)
