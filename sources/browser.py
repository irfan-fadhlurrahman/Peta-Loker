"""BrowserSource: for sites whose job data only exists after JavaScript runs.

A headless Chromium (Playwright, sync API) renders each page and fetch()
returns the rendered DOM, so parse() can read what a visitor's browser would
see — including JSON-LD and data that a plain HTTP GET doesn't contain.

The same rules as HttpClient apply: robots.txt is checked for every URL
(through the source's HttpClient), the same honest User-Agent is sent, and
there is a random delay between page loads. Images, fonts and media are not
downloaded — they aren't needed and would only add load on the site.
"""

from __future__ import annotations

import random
import time

from core.base import JobSource
from core.errors import FetchFailed, RobotsDisallowed, SourceBlocked

BLOCKED_RESOURCES = {"image", "font", "media"}
NAVIGATION_TIMEOUT_MS = 45_000


class BrowserSource(JobSource):
    """Subclasses use self.render(url) in list_jobs() if the listing itself
    needs a browser; fetch() renders detail pages."""

    # Extra milliseconds to let client-side rendering finish after load.
    settle_ms: int = 1500

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._playwright = None
        self._browser = None
        self._page = None
        self._last_load: float | None = None

    @property
    def page(self):
        if self._page is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            context = self._browser.new_context(
                user_agent=self.settings["user_agent"], locale="id-ID", timezone_id="Asia/Jakarta"
            )
            context.route("**/*", self._route)
            self._page = context.new_page()
            self._page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        return self._page

    @staticmethod
    def _route(route):
        if route.request.resource_type in BLOCKED_RESOURCES:
            return route.abort()
        return route.continue_()

    def _wait_turn(self) -> None:
        if self._last_load is None:
            return
        gap = random.uniform(float(self.settings.get("delay_min", 2)), float(self.settings.get("delay_max", 5)))
        remaining = gap - (time.monotonic() - self._last_load)
        if remaining > 0:
            time.sleep(remaining)

    def render(self, url: str, scrolls: int = 0) -> str:
        """Load `url` in the browser (robots-checked, rate-limited) and return
        the rendered HTML. `scrolls` > 0 scrolls down that many times to let
        infinite-scroll listings load more items."""
        if not self.http.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        self._wait_turn()
        try:
            response = self.page.goto(url, wait_until="domcontentloaded")
            status = response.status if response else 0
            if status in (403, 429):
                raise SourceBlocked(f"{status} rendering {url}")
            if status >= 400:
                raise FetchFailed(f"HTTP {status} rendering {url}")
            self.page.wait_for_timeout(self.settle_ms)
            for _ in range(scrolls):
                self.page.mouse.wheel(0, 6000)
                self.page.wait_for_timeout(self.settle_ms)
            return self.page.content()
        except (SourceBlocked, FetchFailed):
            raise
        except Exception as e:  # Playwright timeouts / navigation errors
            raise FetchFailed(f"{type(e).__name__} rendering {url}: {e}") from e
        finally:
            self._last_load = time.monotonic()

    def fetch(self, url: str) -> str:
        return self.render(url)

    def close(self) -> None:
        super().close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._page = self._browser = self._playwright = None
