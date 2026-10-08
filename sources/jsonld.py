"""JsonLdSource: one generic parser for every site that publishes
schema.org/JobPosting JSON-LD (the structured data Google for Jobs reads).

Sites embed JSON-LD in many shapes — a bare object, a list, an "@graph",
nested values as strings or objects, HTML in text fields — so extraction is
defensive and the field mapping lives here once. A subclass only says where
its job pages are (list_jobs), how to read the job id from a URL, and,
optionally, which extra fields to scrape from the HTML (extract_extra).
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from core.base import JobPosting, JobSource
from core.errors import LayoutChanged
from core.text import collapse_ws, html_to_text

SALARY_UNITS = {"HOUR": "hour", "DAY": "day", "WEEK": "week", "MONTH": "month", "YEAR": "year"}
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def iter_jsonld(soup: BeautifulSoup) -> Iterator[dict]:
    """Every JSON object found in <script type="application/ld+json"> tags,
    flattened out of lists and @graph containers."""
    for tag in soup.find_all("script", type="application/ld+json"):
        text = tag.string or tag.get_text() or ""
        if not text.strip():
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            try:  # some sites ship trailing commas
                data = json.loads(_TRAILING_COMMA.sub(r"\1", text))
            except json.JSONDecodeError:
                continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if "@graph" in item:
                    stack.append(item["@graph"])
                yield item


def is_job_posting(obj: dict) -> bool:
    kind = obj.get("@type")
    return kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind)


def _text(value) -> str | None:
    """A JSON-LD value that may be a string, a {name/value} object or a list."""
    if value is None:
        return None
    if isinstance(value, list):
        parts = [_text(v) for v in value]
        return ", ".join(p for p in parts if p) or None
    if isinstance(value, dict):
        for key in ("name", "credentialCategory", "value", "description"):
            if value.get(key) not in (None, ""):
                return _text(value[key])
        return None
    text = collapse_ws(html.unescape(str(value)))
    return text or None


def _street_addresses(posting: dict) -> str | None:
    """The employer's office address lines — used only to pin an ambiguous
    city to its regency during normalisation; never exported."""
    places = posting.get("jobLocation") or []
    places = [places] if isinstance(places, dict) else places
    streets = [collapse_ws(str((p.get("address") or {}).get("streetAddress") or ""))
               for p in places if isinstance(p, dict) and isinstance(p.get("address"), dict)]
    return " | ".join(s for s in streets if s) or None


def _location(posting: dict) -> tuple[str | None, bool]:
    """'Locality, Region' for each jobLocation (joined with ' | '), and
    whether the job is remote."""
    remote = str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE"
    places = posting.get("jobLocation") or []
    if isinstance(places, dict):
        places = [places]
    labels = []
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get("address") or {}
        if isinstance(address, str):
            labels.append(collapse_ws(address))
            continue
        parts = [address.get("addressLocality"), address.get("addressRegion")]
        label = ", ".join(collapse_ws(str(p)) for p in parts if p)
        if label:
            labels.append(label)
    labels = list(dict.fromkeys(labels))  # keep order, drop duplicates
    return (" | ".join(labels) or None), remote


def _salary(posting: dict) -> tuple[str | None, int | None, int | None, str | None]:
    base = posting.get("baseSalary")
    if not isinstance(base, dict):
        return (_text(base), None, None, None) if base else (None, None, None, None)
    currency = base.get("currency") or ""
    value = base.get("value")
    lo = hi = None
    unit = None
    if isinstance(value, dict):
        lo = value.get("minValue", value.get("value"))
        hi = value.get("maxValue", value.get("value"))
        unit = value.get("unitText")
    elif value is not None:
        lo = hi = value
    unit = unit or base.get("unitText")
    lo, hi = _to_int(lo), _to_int(hi)
    if lo is None and hi is None:
        return None, None, None, None
    period = SALARY_UNITS.get(str(unit).upper()) if unit else None
    span = f"{lo}" if lo == hi or hi is None else f"{lo}-{hi}"
    raw = collapse_ws(f"{currency} {span}" + (f"/{period}" if period else ""))
    return raw, lo, hi, period


def _to_int(value) -> int | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return int(number) if number > 0 else None


def _experience(posting: dict) -> str | None:
    value = posting.get("experienceRequirements")
    if isinstance(value, dict) and value.get("monthsOfExperience") not in (None, ""):
        return f"{value['monthsOfExperience']} bulan"
    return _text(value)


def _description(posting: dict) -> str | None:
    """description, or — for sites that split it — responsibilities and
    qualifications, as plain text."""
    parts = []
    for key, heading in (("description", None), ("responsibilities", "Tanggung jawab"),
                         ("qualifications", "Kualifikasi"), ("skills", "Keahlian")):
        value = posting.get(key)
        if not value:
            continue
        text = html_to_text(value if isinstance(value, str) else _text(value))
        if text:
            parts.append(f"{heading}:\n{text}" if heading and parts else text)
    return "\n\n".join(parts) or None


def posting_from_jsonld(obj: dict, source: str, url: str, job_id: str) -> JobPosting:
    location, remote = _location(obj)
    salary_raw, lo, hi, period = _salary(obj)
    extra = {k: _text(obj.get(k)) for k in ("industry", "occupationalCategory") if obj.get(k)}
    identifier = obj.get("identifier")
    if isinstance(identifier, dict) and identifier.get("value"):
        extra["identifier"] = str(identifier["value"])
    if remote:
        extra["remote"] = True
    if street := _street_addresses(obj):
        extra["street_address"] = street
    return JobPosting(
        source=source,
        source_job_id=job_id,
        url=url,
        title=_text(obj.get("title")) or "",
        company_name=_text(obj.get("hiringOrganization")),
        location_raw=location or ("Remote" if remote else None),
        salary_raw=salary_raw,
        salary_min=lo,
        salary_max=hi,
        salary_period=period,
        education_raw=_text(obj.get("educationRequirements")),
        experience_raw=_experience(obj),
        employment_type=_text(obj.get("employmentType")),
        description=_description(obj),
        posted_at=_text(obj.get("datePosted")),
        valid_through=_text(obj.get("validThrough")),
        extra=extra,
    )


class JsonLdSource(JobSource):
    """Parse = find the JobPosting JSON-LD on the page. Subclasses implement
    list_jobs() and job_id_from_url(), and may override extract_extra()."""

    def parse(self, raw: str, url: str) -> list[JobPosting]:
        soup = BeautifulSoup(raw, "lxml")
        postings = [obj for obj in iter_jsonld(soup) if is_job_posting(obj)]
        if not postings:
            raise LayoutChanged(f"no JobPosting JSON-LD on {url}")
        job_id = self.job_id_from_url(url) or url
        posting = posting_from_jsonld(postings[0], self.source, url, job_id)
        if not posting.title:
            raise LayoutChanged(f"JobPosting without a title on {url}")
        posting.extra.update(self.extract_extra(soup))
        return [posting]

    def extract_extra(self, soup: BeautifulSoup) -> dict:
        """Hook for fields a site shows in HTML but not in its JSON-LD."""
        return {}
