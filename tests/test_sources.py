"""Each source's parse() on a synthetic fixture shaped like the real site, and
its list_jobs() over a mocked site. Fixtures are hand-written (no scraped
content is committed)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from core.http import HttpClient
from core.timeutil import today_jakarta
from sources.dealls import DeallsSource
from sources.file import FileSource
from sources.glints import GlintsSource
from sources.jsonld import iter_jsonld
from sources.kalibrr import KalibrrSource
from sources.kitalulus import KitaLulusSource, vacancy_record
from sources.lokerid import LokerIdSource

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _make(cls, conn, raw_store, settings, handler=None, **kwargs):
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(404)))
    http = HttpClient.from_settings(settings, transport=transport, sleep=lambda s: None)
    return cls(conn, settings=settings, http=http, raw_store=raw_store, **kwargs)


def _robots_ok(handler):
    def wrapped(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        return handler(request)
    return wrapped


# ------------------------------------------------------------------ parsing


def test_glints_parses_jsonld(conn, raw_store, settings):
    url = "https://glints.com/id/opportunities/jobs/analis-data/c8205815-0000-0000-0000-000000000001"
    source = _make(GlintsSource, conn, raw_store, settings)
    [p] = source.parse(_read("glints_job.html"), url)
    assert p.source_job_id == "c8205815-0000-0000-0000-000000000001"
    assert (p.title, p.company_name, p.location_raw) == ("Analis Data", "PT Contoh Data", "Bandung, Jawa Barat")
    assert (p.salary_min, p.salary_max, p.salary_period) == (7_000_000, 9_000_000, "month")
    assert p.education_raw == "bachelor degree" and p.experience_raw == "24 bulan"
    assert p.extra["occupationalCategory"] == "Data Analyst"
    assert "Menguasai SQL" in p.description


def test_dealls_finds_jobposting_among_other_jsonld(conn, raw_store, settings):
    url = "https://dealls.com/loker/content-creator~pt-contoh-kreatif"
    [p] = _make(DeallsSource, conn, raw_store, settings).parse(_read("dealls_job.html"), url)
    assert p.source_job_id == "content-creator~pt-contoh-kreatif"
    assert p.employment_type == "FULL_TIME, CONTRACTOR"
    assert p.location_raw == "Jakarta"


def test_lokerid_merges_jsonld_and_html_extras(conn, raw_store, settings):
    url = "https://www.loker.id/akuntansi-keuangan/akuntansi-finansial/staff-accounting-contoh.html"
    [p] = _make(LokerIdSource, conn, raw_store, settings).parse(_read("lokerid_job.html"), url)
    assert p.source_job_id == "staff-accounting-contoh"
    assert p.extra["job_level"] == "Staff / Officer"
    assert p.extra["job_function"] == "Staff Accounting"
    assert p.extra["education_listed"] == "Diploma/D1/D2/D3 / Sarjana / S1"
    assert (p.salary_min, p.salary_max) == (3_000_000, 3_000_000)
    assert "Mencatat jurnal" in p.description and "Kualifikasi" in p.description


def test_kalibrr_turns_one_payload_into_many_postings(conn, raw_store, settings):
    source = _make(KalibrrSource, conn, raw_store, settings)
    postings = source.parse(_read("kalibrr_search.json"), "https://www.kalibrr.com/kjs/job_board/search?offset=0")
    assert len(postings) == 2
    first, second = postings
    assert first.url == "https://www.kalibrr.com/c/contoh-syariah/jobs/900001/community-officer"
    assert (first.salary_min, first.salary_max, first.salary_period) == (3_500_000, 4_000_000, "month")
    assert first.education_raw == "SMA/SMK" and first.extra["job_level"] == "Entry level / Junior"
    assert second.education_raw == "Kalibrr 999"  # unknown code kept, not guessed
    assert second.location_raw == "Remote" and second.extra["remote"] is True


def test_kalibrr_empty_payload_ends_the_listing(conn, raw_store, settings):
    source = _make(KalibrrSource, conn, raw_store, settings)
    assert source.parse('{"count": 0, "jobs": []}', "u") == []
    assert list(source.list_jobs()) == []


def test_kitalulus_uses_jsonld_and_the_matching_vacancy_record(conn, raw_store, settings):
    raw = _read("kitalulus_job.html")
    assert vacancy_record(raw, "account-officer-rmqn")["positionName"] == "Account Officer"
    assert vacancy_record(raw, "missing") is None
    source = _make(KitaLulusSource, conn, raw_store, settings)
    [p] = source.parse(raw, "https://www.kitalulus.com/lowongan/detail/account-officer-rmqn")
    assert p.title == "Account Officer"
    assert p.education_raw == "Minimal D3/D4" and p.employment_type == "Kontrak"
    assert p.extra["skills"] == ["Keterampilan Komunikasi", "Keterampilan Penjualan"]
    assert p.location_raw == "Kabupaten Banyuwangi, Jawa Timur"


def test_jsonld_tolerates_graph_and_trailing_commas():
    from bs4 import BeautifulSoup

    html = ('<script type="application/ld+json">{"@graph": [{"@type": "WebPage"}, '
            '{"@type": "JobPosting", "title": "X",}]}</script>')
    kinds = [o.get("@type") for o in iter_jsonld(BeautifulSoup(html, "lxml"))]
    assert "JobPosting" in kinds


# ------------------------------------------------------------------ discovery


def test_glints_walks_job_sitemaps_in_order_and_skips_sourced_and_english(conn, raw_store, settings):
    index = ("<sitemapindex><sitemap><loc>https://glints.com/sitemap_job_id_2.xml</loc></sitemap>"
             "<sitemap><loc>https://glints.com/sitemap_job_id_1.xml</loc></sitemap>"
             "<sitemap><loc>https://glints.com/sitemap_sourced_job_id_1.xml</loc></sitemap></sitemapindex>")
    job = "https://glints.com/id/opportunities/jobs/{}/0000000{}-0000-0000-0000-000000000000"
    maps = {
        "/sitemap_index.xml": index,
        "/sitemap_job_id_1.xml": f"<loc>{job.format('a', 1)}</loc><loc>https://glints.com/id/en/opportunities/jobs/a/x</loc>",
        "/sitemap_job_id_2.xml": f"<loc>{job.format('b', 2)}</loc>",
    }
    source = _make(GlintsSource, conn, raw_store, settings,
                   _robots_ok(lambda r: httpx.Response(200, text=maps.get(r.url.path, ""))))
    assert list(source.list_jobs()) == [job.format("a", 1), job.format("b", 2)]


def test_dealls_stops_at_the_age_window(conn, raw_store, settings):
    today = today_jakarta()
    docs = [
        {"slug": "new-job", "company": {"slug": "pt-a"}, "publishedAt": f"{today.isoformat()}T01:00:00.000Z"},
        {"slug": "old-job", "company": {"slug": "pt-b"},
         "publishedAt": f"{(today - timedelta(days=90)).isoformat()}T01:00:00.000Z"},
    ]
    payload = {"data": {"docs": docs, "totalPages": 5, "page": 1}}
    source = _make(DeallsSource, conn, raw_store, settings,
                   _robots_ok(lambda r: httpx.Response(200, json=payload)))
    assert list(source.list_jobs()) == ["https://dealls.com/loker/new-job~pt-a"]


def test_lokerid_pages_until_no_new_links(conn, raw_store, settings):
    pages = {
        "/cari-lowongan-kerja": '<a href="/a/b/job-one.html">1</a><a href="/kategori/x">x</a>',
        "/cari-lowongan-kerja/page/2": '<a href="https://www.loker.id/a/b/job-two.html">2</a>',
        "/cari-lowongan-kerja/page/3": '<a href="/a/b/job-two.html">dup</a>',
    }
    source = _make(LokerIdSource, conn, raw_store, settings,
                   _robots_ok(lambda r: httpx.Response(200, text=pages.get(r.url.path, ""))))
    assert list(source.list_jobs()) == ["https://www.loker.id/a/b/job-one.html", "https://www.loker.id/a/b/job-two.html"]


# ------------------------------------------------------------------ file source


def test_file_source_runs_the_full_loop_with_relative_dates(conn, raw_store, settings, tmp_path):
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(
        "source_job_id,title,company_name,location_raw,description,posted_days_ago,extra_job_level\n"
        "S1,Barista,PT Contoh,Kota Bandung,Hubungi WA 0812-0000-1111,3,Staff\n"
        "S2,Kasir,PT Contoh,Depok,Lamar via example,90,\n",
        encoding="utf-8",
    )
    source = _make(FileSource, conn, raw_store, settings, path=csv_path)
    result = source.run()
    assert result.counts["n_parsed"] == 2 and result.counts["n_saved"] == 1  # S2 is outside the window
    row = conn.execute("SELECT * FROM job_postings").fetchone()
    assert row["posting_id"] == "sample:S1"
    assert "0812" not in row["description_masked"]
    assert row["posted_at"] == (today_jakarta() - timedelta(days=3)).isoformat()
    assert json.loads(row["extra_json"]) == {"job_level": "Staff"}


def test_committed_sample_parses_cleanly(conn, raw_store, settings):
    sample = Path(__file__).resolve().parent.parent / "data" / "sample" / "jobs.csv"
    if not sample.exists():
        pytest.skip("sample not generated")
    source = _make(FileSource, conn, raw_store, settings, path=sample)
    postings = source.parse(source.fetch(str(sample)), str(sample))
    assert len(postings) >= 500
    assert all(p.posted_at for p in postings)
