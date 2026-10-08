"""Kalibrr (www.kalibrr.com) — ApiSource.

Discovery and parsing in one: the job-search endpoint that Kalibrr's own job
board calls (/kjs/job_board/search, not disallowed by robots.txt) returns
full job records — description, qualifications, salary, location, education
— 15 per request, sorted by freshness. So no detail pages are downloaded at
all: one request is one listing page.

Code tables: Kalibrr publishes education level and job level as numbers
without labels. The labels below were inferred by comparing the codes with
the education each posting states in its own text (e.g. 450 → "Diploma/D3"
in 52 of 52 sampled postings); unknown codes are kept as "Kalibrr <code>"
and the raw code is stored in `extra` either way.
"""

from __future__ import annotations

from collections.abc import Iterator

from core.base import JobPosting
from core.errors import LayoutChanged
from core.text import html_to_text
from sources.api import ApiSource

API_URL = "https://www.kalibrr.com/kjs/job_board/search"
PAGE_SIZE = 15

EDUCATION_CODES = {200: "SMA/SMK", 350: "Diploma", 450: "Diploma (D3)", 550: "Sarjana (S1)", 650: "Magister (S2)"}
JOB_LEVEL_CODES = {100: "Internship / OJT", 200: "Entry level / Junior", 300: "Associate / Supervisor",
                   400: "Mid-senior / Manager", 500: "Senior / Executive"}
SALARY_PERIODS = {"monthly": "month", "month": "month", "daily": "day", "day": "day", "hourly": "hour",
                  "hour": "hour", "weekly": "week", "yearly": "year", "annually": "year"}


class KalibrrSource(ApiSource):
    source = "kalibrr"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exhausted = False

    def list_jobs(self) -> Iterator[str]:
        for offset in range(0, self.max_listing_pages * PAGE_SIZE, PAGE_SIZE):
            if self._exhausted:
                return
            yield (f"{API_URL}?limit={PAGE_SIZE}&offset={offset}&sort=Freshness&country=Indonesia")

    def parse_payload(self, data, url: str) -> list[JobPosting]:
        if not isinstance(data, dict) or "jobs" not in data:
            raise LayoutChanged(f"unexpected Kalibrr payload from {url}")
        jobs = data["jobs"] or []
        if not jobs:
            self._exhausted = True
        return [self._posting(job) for job in jobs]

    def _posting(self, job: dict) -> JobPosting:
        company = job.get("company") or {}
        address = (job.get("google_location") or {}).get("address_components") or {}
        location = ", ".join(p for p in (address.get("city"), address.get("region")) if p) or None
        lo, hi = job.get("base_salary"), job.get("maximum_salary")
        period = SALARY_PERIODS.get(str(job.get("salary_interval") or "").lower())
        salary_raw = None
        if lo or hi:
            span = f"{lo}-{hi}" if lo and hi and lo != hi else f"{lo or hi}"
            salary_raw = f"{job.get('salary_currency') or 'IDR'} {span}" + (f"/{period}" if period else "")
        edu = job.get("education_level")
        level = job.get("work_experience")
        description = "\n\n".join(
            p for p in (html_to_text(job.get("description")), html_to_text(job.get("qualifications"))) if p
        )
        extra = {"education_code": edu, "job_level_code": level, "job_function": job.get("function")}
        if level in JOB_LEVEL_CODES:
            extra["job_level"] = JOB_LEVEL_CODES[level]
        if job.get("is_work_from_home"):
            extra["remote"] = True
        return JobPosting(
            source=self.source,
            source_job_id=str(job["id"]),
            url=f"https://www.kalibrr.com/c/{company.get('code', 'company')}/jobs/{job['id']}/{job.get('slug', '')}",
            title=job.get("name") or "",
            company_name=job.get("company_name") or company.get("name"),
            location_raw=location or ("Remote" if job.get("is_work_from_home") else None),
            salary_raw=salary_raw,
            salary_min=int(lo) if lo else None,
            salary_max=int(hi) if hi else None,
            salary_period=period,
            education_raw=EDUCATION_CODES.get(edu, f"Kalibrr {edu}" if edu else None),
            employment_type=job.get("tenure"),
            description=description or None,
            posted_at=job.get("activation_date"),
            valid_through=job.get("application_end_date"),
            extra={k: v for k, v in extra.items() if v not in (None, "")},
        )
