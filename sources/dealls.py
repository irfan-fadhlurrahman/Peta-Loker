"""Dealls (dealls.com) — JsonLdSource.

Discovery: the public job-search API that dealls.com's own listing page
calls (api.sejutacita.id/v1/explore-job/job), sorted by publishedAt
descending, so paging stops as soon as postings fall outside the 60-day
window. The listing page itself only server-renders its first 18 jobs, and
the sitemap lists ~2,800 URLs without dates (many long expired).

Parsing: the job page's JobPosting JSON-LD.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from core.timeutil import days_ago, to_iso
from sources.jsonld import JsonLdSource

API_URL = "https://api.sejutacita.id/v1/explore-job/job"
PAGE_SIZE = 20
_JOB_URL = re.compile(r"^https://dealls\.com/loker/([^/?#]+)$")


class DeallsSource(JsonLdSource):
    source = "dealls"

    def list_jobs(self) -> Iterator[str]:
        for page in range(1, self.max_listing_pages + 1):
            params = {"page": page, "limit": PAGE_SIZE, "sortParam": "publishedAt", "sortBy": "desc",
                      "published": "true", "status": "active"}
            data = self.http.get(API_URL, params=params).json().get("data") or {}
            docs = data.get("docs") or []
            for doc in docs:
                age = days_ago(to_iso(doc.get("publishedAt")))
                if age is not None and age > self.max_age_days:
                    return  # sorted newest-first: everything after this is older
                company = doc.get("company") or {}
                slug, company_slug = doc.get("slug"), company.get("slug") if isinstance(company, dict) else None
                if slug and company_slug:
                    yield f"https://dealls.com/loker/{slug}~{company_slug}"
            if not docs or page >= int(data.get("totalPages") or 0):
                return

    def job_id_from_url(self, url: str) -> str | None:
        match = _JOB_URL.match(url)
        return match.group(1) if match else None
