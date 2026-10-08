"""The source contract: every job source is a JobSource subclass.

JobSource.run() is a template method — subclasses never override it. It owns
everything that must be identical across sources: the robots check, the page
cap, the freshness skip, raw storage, masking, the 60-day age window, the
database upsert and the run log. A subclass only says where its jobs are
(list_jobs), how to download one (fetch — inherited for plain HTTP) and how
to read one (parse).

    JobSource (ABC)
    ├── JsonLdSource      generic schema.org JobPosting extractor (+ extract_extra hook)
    ├── ApiSource         JSON embedded in the page / returned by an endpoint
    ├── BrowserSource     headless browser, captures the site's own XHR JSON
    └── FileSource        CSV/XLSX import (demo data, backfill)
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from core import db
from core.config import source as source_settings
from core.errors import FetchFailed, LayoutChanged, RobotsDisallowed, SourceBlocked, SourceError
from core.http import HttpClient
from core.masking import company_hmac, mask_text
from core.raw_store import RawStore, get_raw_store
from core.text import collapse_ws, html_to_text, normalise_company, normalise_title
from core.timeutil import days_ago, now_iso, now_jakarta, to_iso

logger = logging.getLogger(__name__)

# A posting already fetched within this many days is not downloaded again
# when it reappears in a listing; seeing it in the listing is enough to know
# it's still live, so only its fetched_at ("last seen") is refreshed.
REFETCH_AFTER_DAYS = 7


@dataclass
class JobPosting:
    """One listing as published by a source, before normalisation."""

    source: str
    source_job_id: str
    url: str
    title: str
    company_name: str | None = None
    location_raw: str | None = None
    salary_raw: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_period: str | None = None
    education_raw: str | None = None
    experience_raw: str | None = None
    employment_type: str | None = None
    description: str | None = None
    posted_at: str | None = None
    valid_through: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def posting_id(self) -> str:
        return f"{self.source}:{self.source_job_id}"


@dataclass
class RunResult:
    run_id: str
    source: str
    status: str = "running"
    error_type: str | None = None
    message: str | None = None
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(db.RUN_COUNT_COLUMNS, 0))


def content_hash(title: str, company: str | None, location: str | None, description: str | None,
                 chars: int = 500) -> str:
    """Hash of what makes two listings the same job — used to spot a repost
    under a new id on the same site. Uses the *masked* description so a
    changed phone number doesn't make a repost look new."""
    basis = "|".join([
        normalise_title(title),
        normalise_company(company),
        normalise_title(location),
        normalise_title((description or "")[:chars]),
    ])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def posting_to_row(posting: JobPosting, raw_key: str | None, fetched_at: str) -> dict:
    """The masked, storable form of a posting (normalised columns are filled
    later by the normalise step). Contact details are masked here, at
    ingestion, so they never reach the clean tables."""
    description = mask_text(html_to_text(posting.description))
    title = collapse_ws(posting.title)
    company = collapse_ws(posting.company_name) or None
    return {
        "posting_id": posting.posting_id,
        "source_id": posting.source,
        "source_job_id": posting.source_job_id,
        "url": posting.url,
        "raw_key": raw_key,
        "title": mask_text(title),
        "company_name": company,
        "company_hmac": company_hmac(company),
        "location_raw": collapse_ws(posting.location_raw) or None,
        "salary_raw": collapse_ws(posting.salary_raw) or None,
        "salary_min": posting.salary_min,
        "salary_max": posting.salary_max,
        "salary_period": posting.salary_period,
        "education_raw": collapse_ws(posting.education_raw) or None,
        "experience_raw": collapse_ws(posting.experience_raw) or None,
        "employment_type_raw": collapse_ws(posting.employment_type) or None,
        "description_masked": description or None,
        "extra_json": json.dumps(posting.extra, ensure_ascii=False, sort_keys=True) if posting.extra else None,
        "content_hash": content_hash(title, company, posting.location_raw, description),
        "posted_at": to_iso(posting.posted_at),
        "valid_through": to_iso(posting.valid_through),
        "fetched_at": fetched_at,
    }


class JobSource(ABC):
    """Base class for every source. Set `source` (the config key) on the
    subclass; implement list_jobs() and parse()."""

    source: str = ""

    def __init__(self, conn: sqlite3.Connection, settings: dict | None = None, http: HttpClient | None = None,
                 raw_store: RawStore | None = None, max_pages: int | None = None):
        if not self.source:
            raise TypeError(f"{type(self).__name__} must set the class attribute `source`")
        self.conn = conn
        self.settings = settings if settings is not None else source_settings(self.source)
        self.http = http or HttpClient.from_settings(self.settings)
        self.raw_store = raw_store or get_raw_store()
        self.max_pages = int(max_pages if max_pages is not None else self.settings.get("max_pages", 300))
        self.max_age_days = int(self.settings.get("max_age_days", 60))
        self.logger = logging.getLogger(f"source.{self.source}")

    # ---------------------------------------------------------- subclass API

    @abstractmethod
    def list_jobs(self) -> Iterator[str]:
        """Yield detail-page URLs (or other fetchable ids), newest first if the
        site allows it, so the page cap keeps the freshest postings."""

    def job_id_from_url(self, url: str) -> str | None:
        """The site's job id if it can be read from the URL alone. Lets run()
        skip re-downloading a posting it fetched recently. None = unknown."""
        return None

    def fetch(self, url: str) -> str:
        """Download one page. HTTP by default; BrowserSource/FileSource override."""
        return self.http.get(url).text

    @abstractmethod
    def parse(self, raw: str, url: str) -> list[JobPosting]:
        """Turn one fetched page into postings (usually exactly one). Raise
        LayoutChanged if the page doesn't have the expected structure."""

    def close(self) -> None:
        self.http.close()

    # ---------------------------------------------------------- template method

    def run(self) -> RunResult:
        result = RunResult(run_id=uuid.uuid4().hex, source=self.source)
        counts = result.counts
        db.upsert_source(self.conn, self.source, f"{type(self).__module__}.{type(self).__name__}",
                         self.settings.get("base_url"), bool(self.settings.get("enabled", True)))
        db.insert_run(self.conn, result.run_id, self.source, now_iso())
        error_types: dict[str, int] = {}
        try:
            for url in self.list_jobs():
                counts["n_listed"] += 1
                if counts["n_fetched"] >= self.max_pages:
                    counts["n_skipped"] += 1
                    continue
                try:
                    self._process(url, counts)
                except (RobotsDisallowed, LayoutChanged, FetchFailed) as e:
                    counts["n_errors"] += 1
                    error_types[e.error_type] = error_types.get(e.error_type, 0) + 1
                    self.logger.warning("%s: %s", e.error_type, e)
                except SourceBlocked:
                    raise
                except Exception as e:  # one bad page must not kill the run
                    counts["n_errors"] += 1
                    error_types["parse_error"] = error_types.get("parse_error", 0) + 1
                    self.logger.exception("unexpected error on %s: %s", url, e)
        except SourceBlocked as e:
            result.status, result.error_type, result.message = "blocked", e.error_type, str(e)
        except SourceError as e:  # raised by list_jobs itself
            result.status, result.error_type, result.message = "failed", e.error_type, str(e)
        except Exception as e:
            self.logger.exception("run failed")
            result.status, result.error_type, result.message = "failed", "unexpected", f"{type(e).__name__}: {e}"
        else:
            result.status, result.error_type = _final_status(counts, error_types)
            if error_types:
                result.message = json.dumps(error_types, sort_keys=True)
        db.finish_run(self.conn, result.run_id, result.status, counts, result.error_type, result.message)
        self.logger.info("%s run %s: %s %s", self.source, result.run_id[:8], result.status, counts)
        return result

    def _process(self, url: str, counts: dict[str, int]) -> None:
        job_id = self.job_id_from_url(url)
        if job_id:
            last = db.posting_fetched_at(self.conn, f"{self.source}:{job_id}")
            if last and _age_days(last) < REFETCH_AFTER_DAYS:
                db.touch_posting(self.conn, f"{self.source}:{job_id}", now_iso())
                counts["n_skipped"] += 1
                return
        raw = self.fetch(url)
        counts["n_fetched"] += 1
        fetched_at = now_iso()
        raw_key = self.raw_store.save(self.source, job_id or url, raw, {"url": url, "fetched_at": fetched_at})
        postings = self.parse(raw, url)
        counts["n_parsed"] += len(postings)
        rows = []
        for posting in postings:
            age = days_ago(to_iso(posting.posted_at))
            if age is not None and age > self.max_age_days:
                counts["n_skipped"] += 1
                continue
            rows.append(posting_to_row(posting, raw_key, fetched_at))
        for row in rows:
            if row["company_hmac"]:
                db.upsert_company(self.conn, row["company_hmac"], row["company_name"])
        counts["n_saved"] += db.upsert_postings(self.conn, rows)


def _age_days(iso_value: str) -> float:
    try:
        then = datetime.fromisoformat(iso_value)
    except ValueError:
        return float("inf")
    return (now_jakarta() - then) / timedelta(days=1)


def _final_status(counts: dict[str, int], error_types: dict[str, int]) -> tuple[str, str | None]:
    if not error_types:
        return "success", None
    dominant = max(error_types, key=error_types.get)
    if counts["n_saved"] == 0 and counts["n_fetched"] > 0 and dominant == "layout_changed":
        return "failed", "layout_changed"
    if counts["n_saved"] == 0:
        return "failed", dominant
    return "partial", dominant
