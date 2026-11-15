"""A stand-in for the platform's site and internal package index, for proving the installer on a desktop.

    python site_standin.py --site DIR --wheels DIR [--host 127.0.0.1] [--port 8765]

It serves what the real ones would:

- `/install.ps1` and every other file under `--site`, the folder `ctzd ci site --out` wrote;
- `/simple/<project>/`, a PEP 503 page for each project with a wheel in `--wheels`, and a redirect to PyPI's page for
  every other project; the wheels themselves under `/wheels/`;
- `/downloads/uv-x86_64-pc-windows-msvc.zip`, `/downloads/python/...` (uv's `python-install-mirror` layout, the
  python-build-standalone release paths) and `/downloads/MinGit-64-bit.zip`, as redirects to their GitHub releases.

Standard library only, so any Python runs it. scripts/prove-install.ps1 starts and stops it.
"""

import argparse
import hashlib
import html
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from http import HTTPStatus
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import TYPE_CHECKING
from urllib.parse import unquote
from wsgiref.simple_server import WSGIServer, make_server

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse, WSGIApplication, WSGIEnvironment

PYPI_SIMPLE = "https://pypi.org/simple"
PYTHON_RELEASES = "https://github.com/astral-sh/python-build-standalone/releases/download"
UV_VERSION = "0.13.7"
MINGIT_URL = "https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/MinGit-2.56.1-64-bit.zip"
_CONTENT_TYPES = {".ps1": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8", ".whl": "application/octet-stream"}


def normalize(name: str) -> str:
    """A project name as PEP 503 compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_project(filename: str) -> str:
    """The project a wheel belongs to, from its file name (`ctzd_common-0.1.0-py3-none-any.whl` is `ctzd-common`)."""
    return normalize(filename.split("-", 1)[0])


def index_wheels(folder: Path) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for wheel in sorted(folder.glob("*.whl")):
        found.setdefault(wheel_project(wheel.name), []).append(wheel)
    return found


def project_page(project: str, wheels: Sequence[Path]) -> str:
    links = "\n".join(f'<a href="/wheels/{html.escape(w.name)}#sha256={hashlib.sha256(w.read_bytes()).hexdigest()}">{html.escape(w.name)}</a><br>' for w in wheels)
    return f"<!DOCTYPE html>\n<html><head><title>Links for {html.escape(project)}</title></head><body>\n<h1>Links for {html.escape(project)}</h1>\n{links}\n</body></html>\n"


def downloads(uv_version: str, mingit_url: str) -> dict[str, str]:
    """Where each fixed download under `/downloads/` redirects to."""
    return {"/downloads/uv-x86_64-pc-windows-msvc.zip": f"https://github.com/astral-sh/uv/releases/download/{uv_version}/uv-x86_64-pc-windows-msvc.zip", "/downloads/MinGit-64-bit.zip": mingit_url}


Answer = tuple[HTTPStatus, str, bytes | Path | None]
"""A status, then a redirect target or content type, then the body or the file to send."""
_NOT_FOUND: Answer = (HTTPStatus.NOT_FOUND, "text/plain", b"not found\n")


def _redirect(url: str) -> Answer:
    return HTTPStatus.FOUND, url, None


def _simple(rest: str, wheels: Mapping[str, Sequence[Path]]) -> Answer:
    project = normalize(rest.strip("/"))
    if project in wheels:
        return HTTPStatus.OK, "text/html; charset=utf-8", project_page(project, wheels[project]).encode()
    return _redirect(f"{PYPI_SIMPLE}/{project}/") if project else _NOT_FOUND


def _file(folder: Path, rest: str) -> Answer:
    target = (folder / rest).resolve()
    if target.is_file() and target.is_relative_to(folder.resolve()):
        return HTTPStatus.OK, _CONTENT_TYPES.get(target.suffix, "application/octet-stream"), target
    return _NOT_FOUND


def route(path: str, *, site: Path, wheels: Mapping[str, Sequence[Path]], redirects: Mapping[str, str]) -> Answer:
    """What answers a request for `path`."""
    if path in redirects:
        return _redirect(redirects[path])
    if path.startswith("/downloads/python/"):
        return _redirect(f"{PYTHON_RELEASES}/{path.removeprefix('/downloads/python/')}")
    if path.startswith("/simple/"):
        return _simple(path.removeprefix("/simple/"), wheels)
    if path.startswith("/wheels/"):
        found = {w.name: w for ws in wheels.values() for w in ws}.get(path.removeprefix("/wheels/"))
        return (HTTPStatus.OK, _CONTENT_TYPES[".whl"], found) if found else _NOT_FOUND
    return _file(site, path.lstrip("/"))


class _Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def application(*, site: Path, wheels: Mapping[str, Sequence[Path]], redirects: Mapping[str, str]) -> WSGIApplication:
    def answer(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        status, detail, content = route(unquote(str(environ.get("PATH_INFO", "/"))), site=site, wheels=wheels, redirects=redirects)
        line = f"{status.value} {status.phrase}"
        if status == HTTPStatus.FOUND:
            start_response(line, [("Location", detail), ("Content-Length", "0")])
            return [b""]
        data = content.read_bytes() if isinstance(content, Path) else (content or b"")
        start_response(line, [("Content-Type", detail), ("Content-Length", str(len(data)))])
        return [b""] if environ.get("REQUEST_METHOD") == "HEAD" else [data]

    return answer


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A stand-in for the platform's site and internal package index.")
    parser.add_argument("--site", type=Path, required=True, help="the folder `ctzd ci site --out` wrote")
    parser.add_argument("--wheels", type=Path, required=True, help="the locally built wheels")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--uv-version", default=UV_VERSION)
    parser.add_argument("--mingit-url", default=MINGIT_URL)
    args = parser.parse_args(argv)
    wheels = index_wheels(args.wheels)
    if not wheels:
        sys.stderr.write(f"No wheels in {args.wheels}.\n")
        return 2
    server = make_server(args.host, args.port, application(site=args.site, wheels=wheels, redirects=downloads(args.uv_version, args.mingit_url)), server_class=_Server)
    sys.stderr.write(f"Serving {args.site} and {', '.join(sorted(wheels))} on http://{args.host}:{args.port}\n")
    sys.stderr.flush()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
