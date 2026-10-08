"""Glints (glints.com/id) — JsonLdSource.

Discovery: Glints publishes job sitemaps (sitemap_job_id_1.xml … _N.xml,
listed in sitemap_index.xml). They carry no dates, but sitemap 1 holds the
newest postings, so walking them in order is newest-first. Sitemaps have no
paging, so one "listing page" is counted as 30 entries (one page of Glints'
own search results); the walk stops after max_listing_pages of them, or
earlier at the 60-day window.
The explore page with query parameters is disallowed by robots.txt and is
not used. `sitemap_sourced_job_id_*` (jobs Glints aggregates from elsewhere)
are skipped — only Glints' own postings are collected.

Parsing: the job page's JobPosting JSON-LD (salary, region, industry,
occupational category are all there).
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from sources.jsonld import JsonLdSource

SITEMAP_INDEX = "https://glints.com/sitemap_index.xml"
_JOB_SITEMAP = re.compile(r"sitemap_job_id_(\d+)\.xml$")
_JOB_URL = re.compile(r"^https://glints\.com/id/opportunities/jobs/[^/]+/([0-9a-f-]{36})/?$")
PAGE_SIZE = 30
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


class GlintsSource(JsonLdSource):
    source = "glints"

    def list_jobs(self) -> Iterator[str]:
        index = self.http.get(SITEMAP_INDEX).text
        sitemaps = sorted(
            (int(m.group(1)), url) for url in _LOC.findall(index) if (m := _JOB_SITEMAP.search(url))
        )
        seen: set[str] = set()
        limit = self.max_listing_pages * PAGE_SIZE
        for _, sitemap_url in sitemaps:
            for url in _LOC.findall(self.http.get(sitemap_url).text):
                if _JOB_URL.match(url) and url not in seen:  # Indonesian pages only; /id/en/ duplicates skipped
                    seen.add(url)
                    yield url
                    if len(seen) >= limit:
                        return

    def job_id_from_url(self, url: str) -> str | None:
        match = _JOB_URL.match(url)
        return match.group(1) if match else None
