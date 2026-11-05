"""`ctzd ci smoke`: open the live dashboard in a browser, fail on an error box, save a screenshot.

Needs the `ctzd[ci]` extra and a Playwright Chromium on the Jenkins agent.
"""

from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page, Response, sync_playwright

from ctzd_cli.appconfig import Ui

_ERROR_SELECTORS: dict[Ui, tuple[str, ...]] = {"streamlit": ('[data-testid="stException"]',), "dash": (".dash-fe-error__title", '[data-dash-is-loading="true"] .dash-fe-error'), "none": ()}
_READY_SELECTORS: dict[Ui, str] = {"streamlit": '[data-testid="stApp"]', "dash": "#react-entry-point", "none": "body"}
_SETTLE_MS = 4_000
_SERVER_ERROR = 500
_TIMEOUT_MS = 60_000


@dataclass
class SmokeResult:
    pages: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _watch_server_errors(page: Page, result: SmokeResult) -> None:
    """Record any 5xx the page's own requests get, such as a Dash callback that raised, which shows no error box outside debug mode."""

    def on_response(response: Response) -> None:
        if response.status >= _SERVER_ERROR:
            result.problems.append(f"{response.request.method} {response.url} answered {response.status}")

    page.on("response", on_response)


def _check(page: Page, url: str, ui: Ui, result: SmokeResult) -> None:
    response = page.goto(url, wait_until="networkidle", timeout=_TIMEOUT_MS)
    result.pages.append(url)
    if response is None or not response.ok:
        result.problems.append(f"{url} answered {None if response is None else response.status}")
        return
    page.wait_for_selector(_READY_SELECTORS[ui], timeout=_TIMEOUT_MS)
    page.wait_for_timeout(_SETTLE_MS)
    for selector in _ERROR_SELECTORS[ui]:
        errors = page.locator(selector)
        if errors.count():
            result.problems.append(f"{url}: {errors.first.inner_text(timeout=5_000)[:500]}")


def smoke(url: str, *, ui: Ui, screenshot: Path) -> SmokeResult:
    result = SmokeResult()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _watch_server_errors(page, result)
            _check(page, url, ui, result)
            page.screenshot(path=str(screenshot), full_page=True)
            if ui == "streamlit":
                links = page.locator('[data-testid="stSidebarNavLink"]')
                others = [href for href in (links.nth(i).get_attribute("href") for i in range(links.count())) if href is not None and href.rstrip("/") != url.rstrip("/")]
                for href in others:
                    _check(page, href, ui, result)
        finally:
            browser.close()
    return result
