"""`ctzd shot [PATH]`: a picture of a page that `ctzd dev` serves, and the problems on it.

It opens the page in a headless browser (on Windows, the Edge every desktop has,
so nothing is downloaded), waits until parallax has drawn its grids and charts,
writes a PNG under `.ctzd/shots/` for the agent to look at, and reports each
problem as one line: an error the page logs or throws, a request that fails, a
chart or grid with nothing in it, and a chart drawn too small to read. It is not
a gate: `ctzd check` does not run it.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Browser, Page, Playwright, Request, Response, ViewportSize, sync_playwright
from playwright.sync_api import Error as PlaywrightError

SHOTS = Path(".ctzd") / "shots"
_VIEWPORT = ViewportSize(width=1440, height=900)
_LOAD_MS = 60_000
_SETTLE_MS = 1_000
_DRAW_LIMIT_MS = 20_000
_BAD_STATUS = 400
# Taller pictures are cut here, so the agent can still read their text.
_MAX_HEIGHT = 2_400
_SMALL_CHART_PX = 120
_BOX_SIDES = 2  # a canvas's [width, height]
# parallax describes each grid and chart in text for screen readers, which says what it drew.
_EMPTY = re.compile(r"No groups to show|\b0 groups\b|\b0 rows\b")
_TEXT_JS = "() => document.body.innerText"
_CANVASES_JS = """() => Array.from(document.querySelectorAll('canvas')).map((canvas) => {
    const box = canvas.getBoundingClientRect();
    return [Math.round(box.width), Math.round(box.height)];
})"""
_HEIGHT_JS = "() => document.documentElement.scrollHeight"


@dataclass
class Shot:
    url: str
    picture: Path
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def picture_path(root: Path, page_path: str) -> Path:
    """Where the picture of `page_path` goes: `.ctzd/shots/index.png` for `/`, `.ctzd/shots/bonds-x.png` for `/bonds/x`."""
    name = re.sub(r"[^a-z0-9]+", "-", page_path.lower()).strip("-") or "index"
    return root / SHOTS / f"{name}.png"


def _launch(playwright: Playwright) -> Browser:
    if sys.platform == "win32":
        try:
            return playwright.chromium.launch(channel="msedge")
        except PlaywrightError:
            pass  # no Edge: fall back to Playwright's own Chromium
    return playwright.chromium.launch()


def _watch(page: Page, shot: Shot) -> None:
    def on_console(message: object) -> None:
        kind = getattr(message, "type", "")
        if kind == "error":
            shot.problems.append(f"the page logged an error: {str(getattr(message, 'text', ''))[:300]}")

    def on_error(error: PlaywrightError) -> None:
        shot.problems.append(f"the page threw: {str(error).splitlines()[0][:300]}")

    def on_response(response: Response) -> None:
        if response.status >= _BAD_STATUS:
            shot.problems.append(f"{response.request.method} {response.url} answered {response.status}")

    def on_failed(request: Request) -> None:
        shot.problems.append(f"{request.method} {request.url} failed: {request.failure or 'no answer'}")

    page.on("console", on_console)
    page.on("pageerror", on_error)
    page.on("response", on_response)
    page.on("requestfailed", on_failed)


def _settle(page: Page) -> str:
    """The page's text once it stops changing: parallax draws after the page loads, and says what it drew."""
    waited = 0
    before = ""
    while waited < _DRAW_LIMIT_MS:
        page.wait_for_timeout(_SETTLE_MS)
        waited += _SETTLE_MS
        text = str(page.evaluate(_TEXT_JS))
        if text == before and text.strip():
            return text
        before = text
    return before


def _inspect(page: Page, shot: Shot) -> None:
    text = _settle(page)
    for line in text.splitlines():
        if _EMPTY.search(line):
            shot.problems.append(f"a grid or chart drew nothing: {line.strip()[:200]}")
    canvases: object = page.evaluate(_CANVASES_JS)
    if isinstance(canvases, list):
        for box in canvases:
            if isinstance(box, list) and len(box) == _BOX_SIDES and isinstance(box[1], int | float) and 0 < box[1] < _SMALL_CHART_PX:
                shot.problems.append(f'a grid or chart is {int(box[1])} px tall: give its container a height, such as <div className="h-80">')


def _picture(page: Page, path: Path) -> None:
    height: object = page.evaluate(_HEIGHT_JS)
    if isinstance(height, int | float) and height > _VIEWPORT["height"]:
        page.set_viewport_size(ViewportSize(width=_VIEWPORT["width"], height=min(int(height), _MAX_HEIGHT)))
        page.wait_for_timeout(_SETTLE_MS)
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path))


def shoot(url: str, picture: Path) -> Shot:
    """Load `url`, wait for it to draw, write `picture`, and list what is wrong on the page."""
    shot = Shot(url=url, picture=picture)
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = browser.new_page(viewport=_VIEWPORT)
            _watch(page, shot)
            try:
                page.goto(url, wait_until="networkidle", timeout=_LOAD_MS)
            except PlaywrightError as err:
                shot.problems.append(f"{url} did not load: {str(err).splitlines()[0]}")
                return shot
            _inspect(page, shot)
            _picture(page, picture)
        finally:
            browser.close()
    return shot
