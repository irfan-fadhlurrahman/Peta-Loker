from __future__ import annotations

import httpx
import pytest

from core.errors import FetchFailed, RobotsDisallowed, SourceBlocked
from core.http import HttpClient

ROBOTS = "User-agent: *\nDisallow: /private/\n"


def _client(handler, **kwargs) -> HttpClient:
    return HttpClient(user_agent="PetaLokerBot/test", delay_min=0, delay_max=0, max_retries=3,
                      transport=httpx.MockTransport(handler), sleep=lambda s: None, **kwargs)


def test_robots_disallow_raises_before_fetching():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text=ROBOTS if request.url.path == "/robots.txt" else "ok")

    http = _client(handler)
    with pytest.raises(RobotsDisallowed):
        http.get("https://site.test/private/job/1")
    assert calls == ["/robots.txt"]
    assert http.get("https://site.test/job/1").text == "ok"


def test_robots_404_means_no_restrictions_and_5xx_means_disallow_all():
    def handler(request):
        if request.url.host == "open.test":
            return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, text="ok")
        return httpx.Response(503)

    http = _client(handler)
    assert http.allowed("https://open.test/anything")
    assert not http.allowed("https://down.test/anything")


def test_retries_5xx_then_succeeds():
    attempts = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts["n"] += 1
        return httpx.Response(502) if attempts["n"] < 3 else httpx.Response(200, text="done")

    assert _client(handler).get("https://site.test/job").text == "done"
    assert attempts["n"] == 3


def test_403_raises_blocked_immediately():
    def handler(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(403)

    with pytest.raises(SourceBlocked):
        _client(handler).get("https://site.test/job")


def test_persistent_429_raises_blocked():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429, headers={"Retry-After": "1"})

    with pytest.raises(SourceBlocked):
        _client(handler).get("https://site.test/job")


def test_404_is_a_per_item_failure():
    def handler(request):
        return httpx.Response(404)

    with pytest.raises(FetchFailed):
        _client(handler).get("https://site.test/gone")


def test_sends_honest_user_agent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200)

    _client(handler).get("https://site.test/job")
    assert seen["ua"] == "PetaLokerBot/test"
