"""`ctzd ci smoke`: open the live dashboard in a headless browser and fail on an error box, a 5xx, or a page that never loads.

The 5xx are those the page's own requests get. Every problem is reported as text.

Needs the `ctzd[ci]` extra and a Playwright Chromium on the Jenkins agent.
"""

from dataclasses import dataclass, field

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Response, ViewportSize, sync_playwright

from ctzd_cli.appconfig import Ui

_ERROR_SELECTORS: dict[Ui, tuple[str, ...]] = {
    "streamlit": ('[data-testid="stException"]',),
    "dash": (".dash-fe-error__title", '[data-dash-is-loading="true"] .dash-fe-error'),
    "react": (),
    "none": (),
}
_READY_SELECTORS: dict[Ui, str] = {"streamlit": '[data-testid="stApp"]', "dash": "#react-entry-point", "react": "#root > *", "none": "body"}
_SETTLE_MS = 4_000
_VIEWPORT = ViewportSize(width=1440, height=900)
_SERVER_ERROR = 500
_TIMEOUT_MS = 60_000


@dataclass
class SmokeResult:
    pages: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _watch_page_errors(page: Page, result: SmokeResult) -> None:
    """Record any error a React page throws and does not catch; its shell shows no error box of its own to look for."""

    def on_error(error: PlaywrightError) -> None:
        result.problems.append(f"the page threw: {str(error).splitlines()[0][:500]}")

    page.on("pageerror", on_error)


def _watch_server_errors(page: Page, result: SmokeResult) -> None:
    """Record any 5xx the page's own requests get, such as a Dash callback that raised, which shows no error box outside debug mode."""

    def on_response(response: Response) -> None:
        if response.status >= _SERVER_ERROR:
            result.problems.append(f"{response.request.method} {response.url} answered {response.status}")

    page.on("response", on_response)


def _check(page: Page, url: str, ui: Ui, result: SmokeResult, timeout_ms: int) -> None:
    result.pages.append(url)
    try:
        response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
    except PlaywrightError as err:
        result.problems.append(f"{url} did not load: {str(err).splitlines()[0]}")
        return
    if response is None or not response.ok:
        result.problems.append(f"{url} answered {None if response is None else response.status}")
        return
    try:
        page.wait_for_selector(_READY_SELECTORS[ui], timeout=timeout_ms)
    except PlaywrightError:
        seconds = timeout_ms // 1000
        result.problems.append(f"{url}: the page did not finish loading within {seconds} s; a slow query or a page that never stops running are the usual causes")
        return
    page.wait_for_timeout(_SETTLE_MS)
    for selector in _ERROR_SELECTORS[ui]:
        errors = page.locator(selector)
        if errors.count():
            result.problems.append(f"{url}: {errors.first.inner_text(timeout=5_000)[:500]}")


def smoke(url: str, *, ui: Ui, timeout_ms: int = _TIMEOUT_MS) -> SmokeResult:
    """Every problem the dashboard shows, as text; for Streamlit, its other pages are checked too when the first one is fine."""
    result = SmokeResult()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport=_VIEWPORT)
            _watch_server_errors(page, result)
            if ui == "react":
                _watch_page_errors(page, result)
            _check(page, url, ui, result, timeout_ms)
            if ui == "streamlit" and not result.problems:
                links = page.locator('[data-testid="stSidebarNavLink"]')
                others = [href for href in (links.nth(i).get_attribute("href") for i in range(links.count())) if href is not None and href.rstrip("/") != url.rstrip("/")]
                for href in others:
                    _check(page, href, ui, result, timeout_ms)
        finally:
            browser.close()
    return result
