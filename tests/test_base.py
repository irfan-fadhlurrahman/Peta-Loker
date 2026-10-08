"""The JobSource template method, driven by a dummy subclass over a mocked
site — the same path every real source takes."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx

from core import db
from core.base import JobPosting, JobSource
from core.errors import LayoutChanged
from core.http import HttpClient
from core.timeutil import today_jakarta

TODAY = today_jakarta()


def _site(pages: dict[str, str], robots: str = "User-agent: *\nDisallow:\n", status: dict[str, int] | None = None):
    status = status or {}

    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if path in status:
            return httpx.Response(status[path])
        if path in pages:
            return httpx.Response(200, text=pages[path])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _page(job_id: str, posted: str, description: str = "Analisis data harian.") -> str:
    return json.dumps({"id": job_id, "title": "Data Analyst", "company": "PT Contoh Jaya", "posted": posted,
                       "location": "Kota Bandung", "description": description})


class DummySource(JobSource):
    source = "dummy"

    def __init__(self, *args, urls: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        self.urls = urls

    def list_jobs(self):
        yield from self.urls

    def job_id_from_url(self, url):
        return url.rsplit("/", 1)[-1]

    def parse(self, raw, url):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LayoutChanged("not JSON") from e
        return [JobPosting(source=self.source, source_job_id=data["id"], url=url, title=data["title"],
                           company_name=data["company"], location_raw=data["location"],
                           description=data["description"], posted_at=data["posted"])]


def _source(conn, raw_store, settings, pages, urls, **site_kwargs):
    http = HttpClient.from_settings(settings, transport=_site(pages, **site_kwargs), sleep=lambda s: None)
    return DummySource(conn, settings=settings, http=http, raw_store=raw_store, urls=urls)


def test_run_saves_masked_postings_and_logs_the_run(conn, raw_store, settings):
    pages = {"/job/1": _page("1", TODAY.isoformat(), "Kirim CV ke hr@contoh.co.id atau WA 0812-3456-7890")}
    result = _source(conn, raw_store, settings, pages, ["https://jobs.example.test/job/1"]).run()

    assert result.status == "success"
    assert result.counts["n_saved"] == 1
    row = conn.execute("SELECT * FROM job_postings").fetchone()
    assert row["posting_id"] == "dummy:1"
    assert "hr@contoh.co.id" not in row["description_masked"]
    assert "0812" not in row["description_masked"]
    assert row["company_hmac"] and row["company_hmac"] != "PT Contoh Jaya"
    assert row["raw_key"].startswith("job_market/dummy/")
    assert "hr@contoh.co.id" in raw_store.load(row["raw_key"])  # raw keeps the original, privately
    run = conn.execute("SELECT * FROM job_run_logs").fetchone()
    assert run["status"] == "success" and run["n_saved"] == 1
    assert db.count(conn, "job_companies") == 1


def test_postings_older_than_window_are_skipped(conn, raw_store, settings):
    old = (TODAY - timedelta(days=61)).isoformat()
    pages = {"/job/1": _page("1", old), "/job/2": _page("2", TODAY.isoformat())}
    urls = ["https://jobs.example.test/job/1", "https://jobs.example.test/job/2"]
    result = _source(conn, raw_store, settings, pages, urls).run()
    assert result.counts["n_saved"] == 1
    assert result.counts["n_skipped"] == 1


def test_page_cap_limits_downloads(conn, raw_store, settings):
    settings["max_pages"] = 2
    pages = {f"/job/{i}": _page(str(i), TODAY.isoformat()) for i in range(5)}
    urls = [f"https://jobs.example.test/job/{i}" for i in range(5)]
    result = _source(conn, raw_store, settings, pages, urls).run()
    assert result.counts["n_fetched"] == 2
    assert result.counts["n_listed"] == 2


def test_recently_fetched_posting_is_touched_not_refetched(conn, raw_store, settings):
    pages = {"/job/1": _page("1", TODAY.isoformat())}
    urls = ["https://jobs.example.test/job/1"]
    _source(conn, raw_store, settings, pages, urls).run()
    second = _source(conn, raw_store, settings, pages, urls).run()
    assert second.counts["n_fetched"] == 0
    assert second.counts["n_skipped"] == 1


def test_bad_page_is_counted_and_run_continues(conn, raw_store, settings):
    pages = {"/job/1": "<html>no json</html>", "/job/2": _page("2", TODAY.isoformat())}
    urls = ["https://jobs.example.test/job/1", "https://jobs.example.test/job/2"]
    result = _source(conn, raw_store, settings, pages, urls).run()
    assert result.status == "partial"
    assert result.error_type == "layout_changed"
    assert result.counts["n_errors"] == 1 and result.counts["n_saved"] == 1


def test_all_pages_broken_marks_layout_changed(conn, raw_store, settings):
    pages = {"/job/1": "<html></html>"}
    result = _source(conn, raw_store, settings, pages, ["https://jobs.example.test/job/1"]).run()
    assert (result.status, result.error_type) == ("failed", "layout_changed")


def test_403_aborts_the_run_as_blocked(conn, raw_store, settings):
    pages = {"/job/2": _page("2", TODAY.isoformat())}
    urls = ["https://jobs.example.test/job/1", "https://jobs.example.test/job/2"]
    result = _source(conn, raw_store, settings, pages, urls, status={"/job/1": 403}).run()
    assert result.status == "blocked"
    assert result.counts["n_saved"] == 0


def test_robots_disallowed_urls_are_not_fetched(conn, raw_store, settings):
    pages = {"/job/1": _page("1", TODAY.isoformat())}
    result = _source(conn, raw_store, settings, pages, ["https://jobs.example.test/job/1"],
                     robots="User-agent: *\nDisallow: /job/\n").run()
    assert result.counts["n_fetched"] == 0
    assert result.error_type == "robots_disallowed"
