"""Polite HTTP client shared by every HTTP-based source.

- One honest User-Agent (config: collection.user_agent) — no stealth, no
  rotating fingerprints.
- robots.txt is fetched once per host and checked before every request.
- A random delay (delay_min..delay_max seconds) between requests to the same
  host, measured from the end of the previous request.
- Retries with exponential backoff on timeouts, connection errors and 5xx;
  429 honours Retry-After. 403 — or a 429 that persists through every retry —
  raises SourceBlocked, which aborts the run instead of hammering a site that
  is refusing us.
"""

from __future__ import annotations

import logging
import random
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from core.errors import FetchFailed, RobotsDisallowed, SourceBlocked

logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 2.0
MAX_RETRY_AFTER_SECONDS = 120


class HttpClient:
    def __init__(self, user_agent: str, delay_min: float = 2.0, delay_max: float = 5.0,
                 timeout_seconds: float = 30.0, max_retries: int = 3,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.user_agent = user_agent
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_retries = max_retries
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "id-ID,id;q=0.9,en;q=0.8"},
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: dict, **kwargs) -> HttpClient:
        return cls(
            user_agent=settings["user_agent"],
            delay_min=float(settings.get("delay_min", 2.0)),
            delay_max=float(settings.get("delay_max", 5.0)),
            timeout_seconds=float(settings.get("timeout_seconds", 30)),
            max_retries=int(settings.get("max_retries", 3)),
            **kwargs,
        )

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------- robots

    def _robots_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            try:
                response = self._client.get(robots_url)
                status = response.status_code
                body = response.text
            except httpx.HTTPError as e:
                # Can't read robots.txt at all: be conservative and allow
                # nothing until a later run can read it.
                logger.warning("robots.txt unreachable for %s (%s): treating as disallow-all", origin, e)
                status, body = 503, ""
            # RFC 9309: 4xx = no restrictions; 5xx/unreachable = full disallow.
            if 400 <= status < 500:
                parser.parse([])
            elif status >= 500:
                parser.parse(["User-agent: *", "Disallow: /"])
            else:
                parser.parse(body.splitlines())
            self._robots[origin] = parser
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        return self._robots_for(url).can_fetch(self.user_agent, url)

    # ------------------------------------------------------------- fetching

    def _wait_turn(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is None:
            return
        gap = random.uniform(self.delay_min, self.delay_max)
        remaining = gap - (time.monotonic() - last)
        if remaining > 0:
            self._sleep(remaining)

    def get(self, url: str, check_robots: bool = True, **kwargs) -> httpx.Response:
        """GET with robots check, per-host delay and retries. Returns the
        successful response; raises RobotsDisallowed / SourceBlocked /
        FetchFailed otherwise."""
        if check_robots and not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        host = urlsplit(url).netloc
        last_error: str = ""
        for attempt in range(1, self.max_retries + 1):
            self._wait_turn(host)
            try:
                response = self._client.get(url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = f"{type(e).__name__}: {e}"
                response = None
            finally:
                self._last_request[host] = time.monotonic()

            if response is not None:
                status = response.status_code
                if status < 400:
                    return response
                if status == 403:
                    raise SourceBlocked(f"403 Forbidden for {url}")
                if status == 404 or status == 410:
                    raise FetchFailed(f"{status} for {url}")
                last_error = f"HTTP {status}"
                if status == 429:
                    retry_after = _retry_after_seconds(response)
                    if attempt == self.max_retries:
                        raise SourceBlocked(f"429 Too Many Requests persisted for {url}")
                    wait = min(retry_after, MAX_RETRY_AFTER_SECONDS)
                    logger.warning("GET %s: 429 Too Many Requests, waiting %.0fs", url, wait)
                    self._sleep(wait)
                    continue
                if status < 500:
                    raise FetchFailed(f"HTTP {status} for {url}")

            if attempt < self.max_retries:
                delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning("GET %s failed (%s), retry %d/%d in %.0fs",
                               url, last_error, attempt, self.max_retries - 1, delay)
                self._sleep(delay)
        raise FetchFailed(f"{url}: {last_error} after {self.max_retries} attempts")


def _retry_after_seconds(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        return max(float(value), 1.0)
    except ValueError:
        return 30.0
