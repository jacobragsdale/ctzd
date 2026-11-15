"""A dash app in a real browser, as the person sees it, checked against `ctzd render`, which is what the agent sees.

    uv run python scripts/browser_check.py APP_DIR --out DIR [--channel msedge] [--ctzd PATH] [--reload]

Run it where `ctzd` works in APP_DIR (the same CTZD_PLATFORM_CONFIG and CTZD_HOME as made the app). It serves the app
with `ctzd dev --fake`, opens every page in Chromium, or in an installed browser with `--channel` (msedge, chrome),
in the light scheme and the dark, and fails a page on:

- a console error, an uncaught error or a failed request; React other than 19;
- the sample-data banner shown or hidden against what render says;
- an output render reports that the browser shows otherwise: a grid's columns, row count and first rows as
  formatted, a stat's text, a chart's traces and axis formats;
- the page wider than the window, or text, chart labels or grid cells that do not read on their background.

Then it sets each select on the page to its first option and checks the page against `ctzd render PAGE ID.value=X`.
`--reload` renames a page while the server runs, waits for the open browser to show the new name with no restart by
hand, and renames it back. It writes a screenshot of each page in each scheme and `report.json` to DIR, and exits 1
when anything failed.
"""

import argparse
import ast
import json
import math
import shutil
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from playwright.sync_api import Browser, ConsoleMessage, Page, Request, Response, ViewportSize, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from pydantic import BaseModel, ConfigDict, ValidationError

SCHEMES: Final = ("light", "dark")
VIEWPORT: Final[ViewportSize] = {"width": 1440, "height": 900}
SETTLE_MS: Final = 20_000
RELOAD_MS: Final = 60_000
TEXT_CONTRAST: Final = 4.5
GRAPHIC_CONTRAST: Final = 3.0
REACT_MAJOR: Final = "19."
HTTP_ERROR: Final = 400
SAMPLE_BANNER: Final = "ctzd-sample-banner"
# sRGB's transfer function: a channel at or under the knee is linear.
SRGB_KNEE: Final = 0.04045

# Every colour the page computes, read as RGB through a canvas, so oklch() and color-mix() read like rgb().
JS_COLOURS: Final = """
() => {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const pen = canvas.getContext('2d', {willReadFrequently: true});
  const rgb = (colour) => { pen.clearRect(0, 0, 1, 1); pen.fillStyle = '#000'; pen.fillStyle = colour; pen.fillRect(0, 0, 1, 1);
    return [...pen.getImageData(0, 0, 1, 1).data]; };
  const behind = (element) => { for (let at = element; at; at = at.parentElement) {
      const colour = getComputedStyle(at).backgroundColor; if (colour !== 'rgba(0, 0, 0, 0)' && colour !== 'transparent') return rgb(colour); }
    return rgb(getComputedStyle(document.body).backgroundColor); };
  const pairs = [];
  const add = (what, element, colour) => { if (element) pairs.push({what, text: rgb(colour), background: behind(element)}); };
  add('page text', document.querySelector('.mantine-AppShell-main') || document.body, getComputedStyle(document.body).color);
  const tick = document.querySelector('.js-plotly-plot .ytick text');
  if (tick) add('chart labels', tick.closest('.js-plotly-plot'), getComputedStyle(tick).fill);
  const cell = document.querySelector('.ag-cell');
  if (cell) add('grid cells', cell, getComputedStyle(cell).color);
  return {pairs, body: rgb(getComputedStyle(document.body).backgroundColor), overflow: document.documentElement.scrollWidth - window.innerWidth};
}
"""
JS_FACTS: Final = """
(banner) => ({
  react: window.React ? window.React.version : null,
  banner: (() => { const shown = document.getElementById(banner); return !!shown && getComputedStyle(shown).display !== 'none'; })(),
})
"""
JS_OUTPUT: Final = """
([id, kind, wanted]) => {
  const node = document.getElementById(id);
  if (!node) return {missing: true};
  if (kind === 'text') return {text: node.innerText.replace(/\\s+/g, ' ').trim()};
  if (kind === 'figure') {
    const plot = node.classList.contains('js-plotly-plot') ? node : node.querySelector('.js-plotly-plot');
    if (!plot || !plot.data) return {missing: true};
    const axes = {};
    for (const [key, axis] of Object.entries(plot.layout || {})) if (/^[xy]axis\\d*$/.test(key)) axes[key] = axis.tickformat || null;
    // Plotly's decoded traces: a figure from Python sends its numbers as base64 arrays, which plot.data keeps.
    const full = plot._fullData || [];
    return {traces: plot.data.map((trace, index) => ({type: trace.type || 'scatter', name: trace.name ?? null,
                                                      points: full[index] ? full[index]._length : (trace.x || trace.y || []).length})),
            axes, drawn: plot.querySelectorAll('.main-svg').length > 0};
  }
  if (kind === 'grid') {
    const api = window.dash_ag_grid && window.dash_ag_grid.getApi(id);
    if (!api) return {missing: true};
    const columns = api.getAllDisplayedColumns();
    const first = [];
    for (let index = 0; index < Math.min(wanted, api.getDisplayedRowCount()); index++) {
      const row = api.getDisplayedRowAtIndex(index); const shown = {};
      for (const column of columns) { const value = api.getCellValue({rowNode: row, colKey: column, useFormatter: true});
        shown[column.getColDef().headerName] = value === null || value === undefined ? '' : String(value); }
      first.push(shown);
    }
    return {columns: columns.map(column => column.getColDef().headerName), rows: api.getDisplayedRowCount(), first,
            painted: node.querySelectorAll('.ag-row').length};
  }
  return {skipped: true};
}
"""
JS_HEADER_SHOWS: Final = "(name) => [...document.querySelectorAll('header a')].some(link => link.innerText.trim() === name)"


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Trace(_Model):
    type: str
    name: str | None = None
    points: int


class Axis(_Model):
    format: str | None = None


class Output(_Model):
    """One output as `ctzd render` reports it (`inapp/drive.py`'s `summary`)."""

    kind: str
    text: str | float | None = None
    traces: tuple[Trace, ...] = ()
    axes: dict[str, Axis] = {}
    rows: int = 0
    columns: tuple[str, ...] = ()
    first: tuple[dict[str, str | float | bool | None], ...] = ()


class Rendered(_Model):
    ok: bool = False
    summary: str = ""
    sample_data: bool = False
    outputs: dict[str, Output] = {}


class Shown(_Model):
    """One output as the browser shows it (`JS_OUTPUT`)."""

    missing: bool = False
    skipped: bool = False
    text: str = ""
    traces: tuple[Trace, ...] = ()
    axes: dict[str, str | None] = {}
    drawn: bool = False
    columns: tuple[str, ...] = ()
    rows: int = 0
    first: tuple[dict[str, str], ...] = ()
    painted: int = 0


class Pair(_Model):
    what: Literal["page text", "chart labels", "grid cells"]
    text: tuple[int, int, int, int]
    background: tuple[int, int, int, int]


class Colours(_Model):
    pairs: tuple[Pair, ...]
    body: tuple[int, int, int, int]
    overflow: int


class Facts(_Model):
    react: str | None
    banner: bool


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    pages: dict[str, dict[str, object]] = field(default_factory=dict)

    def fail(self, where: str, what: str) -> None:
        self.problems.append(f"{where}: {what}")


@dataclass(frozen=True)
class Run:
    """One app served and one browser open: what every check needs."""

    report: Report
    browser: Browser
    app: Path
    ctzd: str
    out: Path
    url: str


def ctzd_json(ctzd: str, app: Path, *args: str) -> dict[str, object]:
    done = subprocess.run([ctzd, *args, "--json"], cwd=app, capture_output=True, text=True, timeout=600, check=False)
    try:
        found = json.loads(done.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "summary": f"ctzd {' '.join(args)} printed no JSON: {done.stdout[-400:]} {done.stderr[-400:]}"}
    if not isinstance(found, dict):
        return {"ok": False, "summary": f"ctzd {' '.join(args)} printed {found!r}"}
    return {str(key): value for key, value in found.items()}


def render(run: Run, path: str, *sets: str) -> Rendered:
    answer = ctzd_json(run.ctzd, run.app, "render", path, *(part for one in sets for part in ("--set", one)))
    try:
        return Rendered.model_validate(answer)
    except ValidationError as error:
        return Rendered(summary=f"render answered in an unexpected shape: {error}")


def _register_page(source: str) -> ast.Call | None:
    return next((node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "register_page"), None)


def _keyword(call: ast.Call, name: str) -> str | None:
    given = next((keyword.value for keyword in call.keywords if keyword.arg == name), None)
    return given.value if isinstance(given, ast.Constant) and isinstance(given.value, str) else None


def page_paths(app: Path) -> dict[str, Path]:
    """Each page's address, from its `dash.register_page(__name__, path=...)`, or Dash's default for its module."""
    found: dict[str, Path] = {}
    for module in sorted((app / "src" / "app" / "pages").glob("*.py")):
        call = _register_page(module.read_text(encoding="utf-8"))
        if call is not None:
            found[_keyword(call, "path") or "/" + module.stem.replace("_", "-")] = module
    return found


def contrast(text: tuple[int, ...], background: tuple[int, ...]) -> float:
    def luminance(colour: tuple[int, ...]) -> float:
        linear = [part / 255 / 12.92 if part / 255 <= SRGB_KNEE else math.pow((part / 255 + 0.055) / 1.055, 2.4) for part in colour[:3]]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(text), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def normal(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def settle(page: Page) -> None:
    """Wait for the page's callbacks: no request in flight and nothing Dash marks as loading."""
    page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    page.wait_for_function("() => !document.querySelector('[data-dash-is-loading=\"true\"]')", timeout=SETTLE_MS)
    page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    page.wait_for_timeout(400)


def compare_figure(report: Report, where: str, key: str, said: Output, shown: Shown) -> None:
    if list(shown.traces) != list(said.traces) or not shown.drawn:
        report.fail(where, f"{key} draws {[trace.model_dump() for trace in shown.traces]} (drawn: {shown.drawn}); render says {[trace.model_dump() for trace in said.traces]}")
    for axis, about in said.axes.items():
        if shown.axes.get(axis) != about.format:
            report.fail(where, f"{key} {axis} ticks as {shown.axes.get(axis)!r}; render says {about.format!r}")


def compare_grid(report: Report, where: str, key: str, said: Output, shown: Shown) -> None:
    if shown.columns != said.columns:
        report.fail(where, f"{key} has columns {list(shown.columns)}; render says {list(said.columns)}")
    if shown.rows != said.rows or (said.rows and not shown.painted):
        report.fail(where, f"{key} shows {shown.rows} rows ({shown.painted} painted); render says {said.rows}")
    for index, (seen, told) in enumerate(zip(shown.first, said.first, strict=False)):
        differ = {header: (seen.get(header), normal(text)) for header, text in told.items() if seen.get(header) != normal(text)}
        if differ:
            report.fail(where, f"{key} row {index + 1} differs, browser against render: {differ}")


def compare(report: Report, where: str, page: Page, rendered: Rendered) -> None:
    """Every output render reports, as the browser shows it."""
    if not rendered.outputs:
        report.fail(where, f"ctzd render reported no outputs: {rendered.summary}")
    for key, said in rendered.outputs.items():
        element = key.partition(".")[0]
        shown = Shown.model_validate(page.evaluate(JS_OUTPUT, [element, said.kind, len(said.first)]))
        if shown.skipped:
            continue
        if shown.missing:
            report.fail(where, f"{key} is not on the page")
        elif said.kind == "text" and shown.text != normal(said.text).strip():
            report.fail(where, f"{key} shows {shown.text!r}; render says {said.text!r}")
        elif said.kind == "figure":
            compare_figure(report, where, key, said, shown)
        elif said.kind == "grid":
            compare_grid(report, where, key, said, shown)


def look(report: Report, where: str, page: Page, scheme: str, rendered: Rendered) -> dict[str, object]:
    facts = Facts.model_validate(page.evaluate(JS_FACTS, SAMPLE_BANNER))
    if not (facts.react or "").startswith(REACT_MAJOR):
        report.fail(where, f"React is {facts.react}, not 19")
    if facts.banner != rendered.sample_data:
        report.fail(where, f"the sample-data banner is {'shown' if facts.banner else 'hidden'}; render says sample_data {rendered.sample_data}")
    colours = Colours.model_validate(page.evaluate(JS_COLOURS))
    if colours.overflow > 1:
        report.fail(where, f"the page is {colours.overflow} px wider than the window")
    dark = contrast(colours.body, (0, 0, 0)) < contrast(colours.body, (255, 255, 255))
    if dark != (scheme == "dark"):
        report.fail(where, f"the page background {colours.body[:3]} does not follow the {scheme} scheme")
    ratios = {pair.what: contrast(pair.text, pair.background) for pair in colours.pairs}
    for what, ratio in ratios.items():
        needed = GRAPHIC_CONTRAST if what == "chart labels" else TEXT_CONTRAST
        if ratio < needed:
            report.fail(where, f"{what} read at {ratio:.1f}:1 on their background, under {needed}:1")
    compare(report, where, page, rendered)
    return {"react": facts.react, "banner": facts.banner, "contrast": {what: round(ratio, 1) for what, ratio in ratios.items()}}


@contextmanager
def watched(report: Report, browser: Browser, scheme: str, where: str) -> Generator[Page]:
    context = browser.new_context(color_scheme="dark" if scheme == "dark" else "light", viewport=VIEWPORT)
    page = context.new_page()

    def console(message: ConsoleMessage) -> None:
        if message.type == "error":
            report.fail(where, f"console error: {message.text[:300]}")

    def failed(request: Request) -> None:
        report.fail(where, f"request failed: {request.method} {request.url} ({request.failure})")

    def answered(response: Response) -> None:
        if response.status >= HTTP_ERROR and not response.url.endswith("/favicon.ico"):
            report.fail(where, f"{response.request.method} {response.url} answered {response.status}")

    page.on("console", console)
    page.on("pageerror", lambda error: report.fail(where, f"uncaught error: {str(error)[:300]}"))
    page.on("requestfailed", failed)
    page.on("response", answered)
    try:
        yield page
    finally:
        context.close()


def choose(report: Report, page: Page, where: str, select_id: str) -> str | None:
    """Open a select and pick its first option; return the option's value."""
    page.locator(f"#{select_id}").click()
    option = page.locator('[role="option"]').first
    option.wait_for(timeout=SETTLE_MS)
    value = option.get_attribute("value") or option.inner_text().strip()
    option.click()
    try:
        settle(page)
    except PlaywrightTimeout as error:
        report.fail(where, f"the page did not settle after choosing {value!r}: {error}")
        return None
    return value


def selects(page: Page) -> list[str]:
    found = page.eval_on_selector_all('input[aria-haspopup="listbox"]', "nodes => nodes.map(node => node.id)")
    return [str(one) for one in found if one] if isinstance(found, list) else []


def check_page(run: Run, path: str) -> None:
    report = run.report
    rendered = render(run, path)
    slug = path.strip("/").replace("/", "-") or "home"
    report.pages[path] = {"render": rendered.summary}
    if not rendered.ok:
        report.fail(path, f"ctzd render failed: {rendered.summary}")
    for scheme in SCHEMES:
        where = f"{path} ({scheme})"
        with watched(report, run.browser, scheme, where) as page:
            started = time.monotonic()
            page.goto(run.url + path, wait_until="domcontentloaded")
            try:
                settle(page)
            except PlaywrightTimeout as error:
                report.fail(where, f"the page did not settle in {SETTLE_MS // 1000} s: {error}")
            report.pages[path][scheme] = look(report, where, page, scheme, rendered) | {"seconds": round(time.monotonic() - started, 1)}
            page.screenshot(path=run.out / f"{slug}-{scheme}.png", full_page=True)
            if scheme != "light":
                continue
            for select_id in selects(page):
                value = choose(report, page, f"{where} after {select_id}", select_id)
                if value is not None:
                    look(report, f"{path} with {select_id} = {value}", page, scheme, render(run, path, f"{select_id}.value={value}"))
                    page.screenshot(path=run.out / f"{slug}-{select_id}.png", full_page=True)


def check_reload(run: Run, pages: dict[str, Path]) -> None:
    """Rename a page in its file while the server runs; the open page's header should follow, with no restart."""
    for path, module in pages.items():
        source = module.read_text(encoding="utf-8")
        call = _register_page(source)
        old = None if call is None else _keyword(call, "name")
        if old is None or source.count(f'name="{old}"') != 1:
            continue
        new = f"{old} reloaded"
        timings: dict[str, float] = {}
        with watched(run.report, run.browser, "light", f"reload {path}") as page:
            page.goto(run.url + path, wait_until="domcontentloaded")
            settle(page)
            try:
                for wanted, text in ((new, source.replace(f'name="{old}"', f'name="{new}"')), (old, source)):
                    started = time.monotonic()
                    module.write_text(text, encoding="utf-8")
                    page.wait_for_function(JS_HEADER_SHOWS, arg=wanted, timeout=RELOAD_MS)
                    timings[wanted] = round(time.monotonic() - started, 1)
            except PlaywrightTimeout:
                run.report.fail(f"reload {path}", f"the open page did not show the edited name within {RELOAD_MS // 1000} s")
            finally:
                module.write_text(source, encoding="utf-8")
        run.report.pages["reload"] = {"page": path, "seconds": timings}
        return
    run.report.fail("reload", 'no page names itself with name="..." in register_page, so there was nothing to rename')


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("app", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--channel", help="an installed browser: msedge or chrome; default: Playwright's Chromium")
    parser.add_argument("--ctzd", default=shutil.which("ctzd") or "ctzd")
    parser.add_argument("--reload", action="store_true", help="also prove the dev server reloads an edited page")
    args = parser.parse_args()
    app, out = Path(args.app).resolve(), Path(args.out).resolve()
    found = shutil.which(str(args.ctzd))
    ctzd = str(Path(found).resolve()) if found is not None else str(args.ctzd)
    out.mkdir(parents=True, exist_ok=True)
    report = Report()
    started = ctzd_json(ctzd, app, "dev", "--fake")
    if not started.get("ok"):
        sys.stdout.write(f"ctzd dev failed: {json.dumps(started)[:2000]}\n")
        return 1
    pages = page_paths(app)
    browser_name = args.channel or "chromium"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.channel)
            browser_name = f"{browser_name} {browser.version}"
            run = Run(report, browser, app, ctzd, out, str(started["url"]).rstrip("/"))
            for path in pages:
                check_page(run, path)
            if args.reload:
                check_reload(run, pages)
            browser.close()
    finally:
        ctzd_json(ctzd, app, "dev", "--stop")
    (out / "report.json").write_text(json.dumps({"browser": browser_name, "problems": report.problems, "pages": report.pages}, indent=2, default=str), encoding="utf-8")
    for line in report.problems or [f"{len(pages)} page(s) passed in {browser_name}, light and dark."]:
        sys.stdout.write(f"{line}\n")
    return 1 if report.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
