"""What the firm's package index and the platform's site must hold for ctzd, and whether an index holds it.

    uv run python scripts/index_check.py --write docs/package-index.md          # the list, resolved from PyPI
    uv run python scripts/index_check.py --index https://pypi.example.net/simple   # does this index hold it?

Desktops (Windows) and the app image (Linux, glibc) install every app's packages from one index, wheels only, on
Python 3.14. The list is the third-party packages of the CLI, ctzd-kit, ctzd-common with its dash extra and the
department kits, and what build.toml adds to every app: its dev tools and its build backend. The platform's own
packages (ctzd, ctzd-kit, ctzd-common, the kits and their libraries) are not in it: the platform publishes those.

With `--index`, it resolves the list against that index for both platforms; when that fails, it looks up each
package PyPI resolves, at its version, and names every one the index lacks a wheel of. The index URL may carry
credentials (https://USER:TOKEN@host/simple). Requests trust the system's certificates, as uv's system-certs does.
"""

import argparse
import json
import re
import shutil
import ssl
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO: Final = Path(__file__).resolve().parents[1]
PYPI: Final = "https://pypi.org/simple"
PYTHON: Final = "3.14"
PLATFORMS: Final = {"Windows desktop": "x86_64-pc-windows-msvc", "Linux image": "x86_64-manylinux_2_28"}
WHEEL_TAGS: Final = {"Windows desktop": ("win_amd64", "none-any"), "Linux image": ("manylinux", "none-any")}
PROJECTS: Final = ("packages/*/pyproject.toml", "kits/*/pyproject.toml", "kits/*/libs/*/pyproject.toml")
BUILD: Final = REPO / "packages/ctzd-cli/src/ctzd_cli/assets/build.toml"
CI: Final = REPO / ".github/workflows/ci.yml"
PIN: Final = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;]+)")


@dataclass(frozen=True)
class Resolved:
    ok: bool
    pins: dict[str, str]
    output: str


def uv_path() -> str:
    found = shutil.which("uv")
    if found is None:
        msg = "uv is not on PATH."
        raise SystemExit(msg)
    return found


def own_projects() -> dict[str, Path]:
    return {canonicalize_name(tomllib.loads(path.read_text(encoding="utf-8"))["project"]["name"]): path for pattern in PROJECTS for path in sorted(REPO.glob(pattern))}


def requirements() -> list[str]:
    """Every third-party requirement of the platform's packages and of every app's build settings."""
    own = own_projects()
    wanted: list[str] = []
    for path in own.values():
        project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        wanted += project.get("dependencies", [])
        for extra in project.get("optional-dependencies", {}).values():
            wanted += extra
    build = tomllib.loads(BUILD.read_text(encoding="utf-8"))
    wanted += [*build["python"]["dev"], build["python"]["build_backend"]]
    for kind in build["kinds"].values():
        wanted += kind["dependencies"]
    third_party = {str(Requirement(text)) for text in wanted if canonicalize_name(Requirement(text).name) not in own}
    # A requirement on one of the platform's own packages with an extra still needs that extra's packages.
    for text in wanted:
        found = Requirement(text)
        if canonicalize_name(found.name) in own and found.extras:
            project = tomllib.loads(own[canonicalize_name(found.name)].read_text(encoding="utf-8"))["project"]
            third_party |= {str(Requirement(item)) for extra in found.extras for item in project.get("optional-dependencies", {}).get(extra, [])}
    return sorted(third_party)


def resolve(index: str, platform: str) -> Resolved:
    command = [
        uv_path(),
        "pip",
        "compile",
        "-",
        "--system-certs",
        "--quiet",
        "--no-header",
        "--no-annotate",
        "--python-version",
        PYTHON,
        "--python-platform",
        platform,
        "--only-binary",
        ":all:",
        "--default-index",
        index,
    ]
    done = subprocess.run(command, input="\n".join(requirements()) + "\n", capture_output=True, text=True, timeout=600, check=False)
    pins = {str(canonicalize_name(match[1])): match[2] for line in done.stdout.splitlines() if (match := PIN.match(line.strip()))}
    return Resolved(done.returncode == 0, pins, (done.stderr or done.stdout).strip())


def files_on(client: httpx.Client, index: str, name: str) -> list[str] | None:
    """The file names an index lists for a project, by the JSON simple API or its HTML page; None when it has none."""
    answer = client.get(f"{index.rstrip('/')}/{name}/", headers={"Accept": "application/vnd.pypi.simple.v1+json, text/html;q=0.1"}, follow_redirects=True)
    if answer.status_code == httpx.codes.NOT_FOUND:
        return None
    answer.raise_for_status()
    if answer.headers.get("content-type", "").startswith("application/vnd.pypi.simple.v1+json"):
        return [str(item["filename"]) for item in answer.json().get("files", [])]
    return [str(found) for found in re.findall(r">([^<>]+\.(?:whl|tar\.gz|zip))</a>", answer.text)]


def wheel_for(files: list[str], name: str, version: str, place: str) -> bool:
    prefix = f"{name.replace('-', '_')}-{version}-".lower()
    return any(file.lower().startswith(prefix) and file.endswith(".whl") and any(tag in file for tag in WHEEL_TAGS[place]) and ("cp314" in file or "abi3" in file or "py3" in file) for file in files)


def missing_from(index: str, wanted: dict[str, dict[str, str]]) -> list[str]:
    lacking: list[str] = []
    with httpx.Client(verify=ssl.create_default_context(), timeout=60) as client:
        for name in sorted(wanted):
            files = files_on(client, index, name)
            for place, version in sorted(wanted[name].items()):
                if files is None:
                    lacking.append(f"{name}: not on the index at all (wanted {version} for the {place})")
                    break
                if not wheel_for(files, name, version, place):
                    lacking.append(f"{name} {version}: no wheel for the {place}")
    return lacking


def downloads() -> list[str]:
    """What the platform's site serves under /downloads, for the uv the platform pins."""
    found = re.search(r'version: "(\d+\.\d+\.\d+)"', CI.read_text(encoding="utf-8"))
    pinned = found[1] if found else "the pinned version"
    pythons = json.loads(subprocess.run([uv_path(), "python", "list", PYTHON, "--all-platforms", "--output-format", "json"], capture_output=True, text=True, timeout=120, check=True).stdout)
    windows = next((item for item in pythons if item.get("os") == "windows" and item.get("arch") == "x86_64" and item.get("implementation") == "cpython" and item.get("variant") == "default"), {})
    url = str(windows.get("url", ""))
    tail = url.split("/releases/download/", 1)[-1] if "/releases/download/" in url else "TAG/cpython-3.14.X+TAG-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    return [
        f"| `/downloads/uv-x86_64-pc-windows-msvc.zip` | uv {pinned}'s Windows build: https://github.com/astral-sh/uv/releases/download/{pinned}/uv-x86_64-pc-windows-msvc.zip |",
        (
            f"| `/downloads/python/{tail}` | Python {windows.get('version', PYTHON)} for Windows, from python-build-standalone ({url}); "
            "uv asks for it under its release tag, so mirror the release folder |"
        ),
        "| `/downloads/MinGit-64-bit.zip` | Git for Windows' MinGit, for desktops with no git: https://github.com/git-for-windows/git/releases |",
        "| `/install.ps1` | `ctzd ci site --out DIR` writes it from platform.toml and build.toml |",
    ]


def document(resolved: dict[str, Resolved]) -> str:
    names = sorted({name for found in resolved.values() for name in found.pins})
    rows = [f"| `{name}` | {' | '.join(resolved[place].pins.get(name, '-') for place in PLATFORMS)} |" for name in names]
    return "\n".join(
        [
            "# What the package index and the site must hold",
            "",
            "Written by `uv run python scripts/index_check.py --write docs/package-index.md` from PyPI on the day of the",
            "commit; run it again after changing a pin. Check the firm's index with",
            "`uv run python scripts/index_check.py --index URL`: it resolves the same list there and names what is missing.",
            "",
            f"Every app installs from one index, wheels only, on Python {PYTHON}: on the Windows desktops and in the Linux image.",
            "The platform's own packages (`ctzd`, `ctzd-kit`, `ctzd-common`, the kits and their libraries) are published to",
            "the index by the platform and are not listed. A newer version on the index is fine where the requirement allows it.",
            "",
            "## Packages",
            "",
            f"| Package | {' | '.join(PLATFORMS)} |",
            f"|---|{'---|' * len(PLATFORMS)}",
            *rows,
            "",
            "## The site's downloads",
            "",
            "| Path | What |",
            "|---|---|",
            *downloads(),
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--index", default=PYPI, help="the index to check; default: PyPI")
    parser.add_argument("--write", type=Path, help="write the list, resolved from --index, as Markdown")
    args = parser.parse_args()
    index = str(args.index)
    resolved = {place: resolve(index, platform) for place, platform in PLATFORMS.items()}
    failed = {place: found for place, found in resolved.items() if not found.ok}
    if args.write is not None and not failed:
        Path(args.write).write_text(document(resolved), encoding="utf-8")
        sys.stdout.write(f"Wrote {args.write}: {len({name for found in resolved.values() for name in found.pins})} packages.\n")
        return 0
    if not failed:
        sys.stdout.write(f"{index} holds every package ctzd needs, for both platforms ({len(resolved['Windows desktop'].pins)} on Windows, {len(resolved['Linux image'].pins)} on Linux).\n")
        return 0
    for place, found in failed.items():
        sys.stdout.write(f"Resolving on {index} failed for the {place}:\n{found.output[-1500:]}\n\n")
    if index != PYPI:
        wanted: dict[str, dict[str, str]] = {}
        for place, platform in PLATFORMS.items():
            for name, version in resolve(PYPI, platform).pins.items():
                wanted.setdefault(name, {})[place] = version
        lacking = missing_from(index, wanted)
        sys.stdout.write("What the index lacks, against what PyPI resolves:\n" + "".join(f"- {line}\n" for line in lacking or ["nothing at these versions: see uv's message above"]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
