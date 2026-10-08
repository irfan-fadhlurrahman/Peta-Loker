"""KitaLulus (www.kitalulus.com) — BrowserSource.

Why a browser: the listing is an infinite scroll with no server-side paging
(?page=N returns the same first 31 jobs), so newer jobs only appear as a
visitor scrolls. The site's data API (gql.kitalulus.com) is disallowed by its
robots.txt, so it is never called directly or read from: everything here
comes from rendering public www.kitalulus.com pages, which robots.txt allows.

Discovery: render /lowongan sorted by "Terbaru" (updatedAt) and scroll,
collecting /lowongan/detail/<slug> links; the first render and each scroll
count as one listing page.

Parsing: the rendered detail page carries a JobPosting JSON-LD block (read
with the shared JSON-LD mapper) and the vacancy record the page was rendered
from, which adds fields the JSON-LD lacks — education wording, experience,
contract type, skill tags and job role.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from core.base import JobPosting
from core.errors import LayoutChanged
from core.text import collapse_ws
from sources.browser import BrowserSource
from sources.jsonld import is_job_posting, iter_jsonld, posting_from_jsonld

BASE_URL = "https://www.kitalulus.com"
LISTING_URL = f"{BASE_URL}/lowongan?sortBy=updatedAt"
IDLE_SCROLLS_BEFORE_STOP = 3
_DETAIL_PATH = re.compile(r"^/lowongan/detail/([a-z0-9-]+)$")
_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.S)


def _rsc_payload(raw: str) -> str:
    """The React Server Components stream a Next.js page inlines as
    self.__next_f.push([1, "<json string>"]) calls, decoded and joined."""
    parts = []
    for chunk in _RSC_CHUNK.findall(raw):
        try:
            parts.append(json.loads(f'"{chunk}"'))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


def vacancy_record(raw: str, slug: str) -> dict | None:
    """The {"vacancy": {...}} object for `slug` inside the RSC payload."""
    payload = _rsc_payload(raw)
    marker = '"vacancy":{"id":'
    start = payload.find(marker)
    while start != -1:
        obj_start = start + len('"vacancy":')
        try:
            record, _ = json.JSONDecoder().raw_decode(payload, obj_start)
        except json.JSONDecodeError:
            record = None
        if isinstance(record, dict) and record.get("slug") == slug:
            return record
        start = payload.find(marker, start + 1)
    return None


class KitaLulusSource(BrowserSource):
    source = "kitalulus"

    def list_jobs(self) -> Iterator[str]:
        self.render(LISTING_URL)
        ordered: list[str] = []
        seen: set[str] = set()
        idle = 0
        for _ in range(self.max_listing_pages):  # one listing page = the first render or one scroll
            hrefs = self.page.eval_on_selector_all(
                'a[href*="/lowongan/detail/"]', "els => els.map(e => e.getAttribute('href'))"
            )
            new = [h for h in dict.fromkeys(hrefs) if h and _DETAIL_PATH.match(h) and h not in seen]
            seen.update(new)
            ordered.extend(new)
            # Collect before yielding: fetch() reuses the same page, which
            # would navigate away from the listing.
            if new:
                idle = 0
            else:
                idle += 1
                if idle >= IDLE_SCROLLS_BEFORE_STOP or len(seen) >= self.max_pages:
                    break
            if len(seen) >= self.max_pages:
                break
            self.page.mouse.wheel(0, 6000)
            self.page.wait_for_timeout(self.settle_ms)
        for href in ordered:
            yield f"{BASE_URL}{href}"

    def job_id_from_url(self, url: str) -> str | None:
        match = _DETAIL_PATH.match(url.replace(BASE_URL, ""))
        return match.group(1) if match else None

    def parse(self, raw: str, url: str) -> list[JobPosting]:
        slug = self.job_id_from_url(url) or url
        soup = BeautifulSoup(raw, "lxml")
        jsonld = next((obj for obj in iter_jsonld(soup) if is_job_posting(obj)), None)
        record = vacancy_record(raw, slug)
        if jsonld is None and record is None:
            raise LayoutChanged(f"no JobPosting JSON-LD or vacancy record on {url}")
        posting = posting_from_jsonld(jsonld or {}, self.source, url, slug)
        if record:
            _merge_record(posting, record)
        if not posting.title:
            raise LayoutChanged(f"no title on {url}")
        return [posting]


def _merge_record(posting: JobPosting, record: dict) -> None:
    """Fill gaps from the page's vacancy record (values as KitaLulus shows
    them, e.g. "Minimal D3/D4", "Kontrak")."""
    company = record.get("company") or {}
    city = (record.get("city") or {}).get("name")
    province = (record.get("province") or {}).get("name")
    posting.title = posting.title or collapse_ws(record.get("positionName"))
    posting.company_name = posting.company_name or company.get("name")
    posting.location_raw = posting.location_raw or (", ".join(p for p in (city, province) if p) or None)
    posting.education_raw = record.get("educationLevelStr") or posting.education_raw
    posting.experience_raw = record.get("minExperienceStr") or posting.experience_raw
    posting.employment_type = record.get("typeStr") or posting.employment_type
    posting.description = posting.description or record.get("formattedDescription") or record.get("description")
    lo, hi = record.get("salaryLowerBound") or None, record.get("salaryUpperBound") or None
    if (lo or hi) and posting.salary_min is None:
        posting.salary_min, posting.salary_max, posting.salary_period = lo, hi, "month"
        posting.salary_raw = f"IDR {lo}-{hi}/month" if lo and hi and lo != hi else f"IDR {lo or hi}/month"
    extra = {
        "skills": record.get("skillTags") or None,
        "job_role": (record.get("jobRole") or {}).get("displayName"),
        "industry": (company.get("companyIndustry") or {}).get("name"),
        "work_site": record.get("locationSiteStr"),
    }
    posting.extra.update({k: v for k, v in extra.items() if v})
