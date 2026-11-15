"""`python -m app`: serve the dashboard with uvicorn, on Dash's FastAPI backend, with its health and readiness routes.
Platform-owned: ctzd sync rewrites this file.

    python -m app --host 0.0.0.0 --port 8080    in the pod
    python -m app --dev --port 8501             on the desktop, through `ctzd dev`: reloads when the code changes
"""

import argparse
import os
import sys
import uuid
from collections.abc import Sequence

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

DEV_ID = "CTZD_DEV_ID"
"""The token `ctzd dev` gives the server, which it answers at `/_ctzd/dev`, so `ctzd dev --stop` stops only its own."""
_DEV = "CTZD_DEV"
_BOOT = "/_ctzd/boot"
# Under `ctzd dev`, every open page asks each second which server process answers it, and loads itself again when a
# new one does: uvicorn starts a new process for each change to the code, and Dash's own hot reload, on the FastAPI
# backend, sees the new process but does not redraw the page.
_RELOAD_SCRIPT = (
    "<script>(() => { let boot = null; const ask = () => fetch('" + _BOOT + "', {cache: 'no-store'})"
    ".then(answer => answer.ok ? answer.text() : null)"
    ".then(now => { if (now && boot && now !== boot) { window.location.reload(); } if (now) { boot = now; } })"
    ".catch(() => null).finally(() => setTimeout(ask, 1000)); ask(); })();</script>"
)


def _dash_variables() -> list[str]:
    """Variables that would change what Dash does behind the app's back, its dev tools in production among them."""
    return sorted(name for name in os.environ if name.startswith("DASH_") or name == "_DASH_FASTAPI_CONFIG")


def server() -> FastAPI:
    """The app's server: Dash's FastAPI app, with `/health/live`, `/health/ready` and, under `ctzd dev`, `/_ctzd/dev`
    and `/_ctzd/boot`, the process's id, by which open pages load themselves again after a change to the code."""
    from app.app import app  # noqa: PLC0415 - built when the server starts, in uvicorn's worker under --dev

    built = app.server
    if not isinstance(built, FastAPI):
        msg = "The app is not on Dash's FastAPI backend: build it with ui.dash_app(TITLE) in app.py."
        raise TypeError(msg)
    dev_mode = os.environ.get(_DEV) == "1"
    if dev_mode:
        # Dash's checks of component properties, without its debug panel, which would sit over the page the person
        # is looking at: errors reach them through `ui.on_error`, and the agent through `ctzd render`.
        app.enable_dev_tools(debug=True, dev_tools_ui=False, dev_tools_hot_reload=False, dev_tools_disable_version_check=True)
        app.index_string = app.index_string.replace("{%renderer%}", "{%renderer%}\n" + _RELOAD_SCRIPT)
    token = os.environ.get(DEV_ID, "")
    boot = uuid.uuid4().hex

    def live() -> PlainTextResponse:
        return PlainTextResponse("ok")

    def dev() -> PlainTextResponse:
        return PlainTextResponse(token)

    def booted() -> PlainTextResponse:
        return PlainTextResponse(boot)

    routes = [("/health/live", live), ("/health/ready", live), *([("/_ctzd/dev", dev)] if token else []), *([(_BOOT, booted)] if dev_mode else [])]
    # Added before Dash's first request sets up its own routes, so they come before its catch-all page route.
    for path, endpoint in routes:
        built.add_api_route(path, endpoint, methods=["GET"], include_in_schema=False)
    return built


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dev", action="store_true", help="reload when the code changes, with Dash's dev tools")
    args = parser.parse_args(argv)
    if found := _dash_variables():
        sys.stderr.write(f"Refusing to start: {', '.join(found)} would change how Dash runs this app. Unset them.\n")
        return 2
    if args.dev:
        os.environ[_DEV] = "1"
        uvicorn.run("app.__main__:server", factory=True, host=args.host, port=args.port, reload=True, reload_dirs=["src"], log_level="info")
        return 0
    uvicorn.run(server(), host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*", log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
