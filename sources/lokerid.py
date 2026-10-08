"""Loker.id (www.loker.id) — JsonLdSource with an extract_extra() hook.

Discovery: the search listing /cari-lowongan-kerja and its /page/N pages
(~21 jobs per page, newest first). Listing pages carry no dates; the run
loop's old-posting rule ends the walk once postings fall outside 60 days.

Parsing: the job page's JobPosting JSON-LD (salary, education, experience
are structured there), plus two fields that only appear in the HTML and help
occupation coding — "Level Pekerjaan" (staff / supervisor / manager…) and
"Fungsi" — read by extract_extra().
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.text import collapse_ws
from sources.jsonld import JsonLdSource

BASE_URL = "https://www.loker.id"
LISTING_URL = f"{BASE_URL}/cari-lowongan-kerja"
MAX_LISTING_PAGES = 150
_JOB_PATH = re.compile(r"^/[a-z0-9-]+/[a-z0-9-]+/([a-z0-9-]+)\.html$")
EXTRA_LABELS = {"Level Pekerjaan": "job_level", "Fungsi": "job_function", "Pendidikan": "education_listed"}


class LokerIdSource(JsonLdSource):
    source = "lokerid"

    def list_jobs(self) -> Iterator[str]:
        seen: set[str] = set()
        for page in range(1, MAX_LISTING_PAGES + 1):
            url = LISTING_URL if page == 1 else f"{LISTING_URL}/page/{page}"
            soup = BeautifulSoup(self.http.get(url).text, "lxml")
            new = []
            for a in soup.find_all("a", href=True):
                path = a["href"].replace(BASE_URL, "")
                if _JOB_PATH.match(path) and path not in seen:
                    seen.add(path)
                    new.append(urljoin(BASE_URL, path))
            if not new:
                return
            yield from new

    def job_id_from_url(self, url: str) -> str | None:
        match = _JOB_PATH.match(url.replace(BASE_URL, ""))
        return match.group(1) if match else None

    def extract_extra(self, soup: BeautifulSoup) -> dict:
        """Label/value pairs rendered as <div class="font-bold">Label</div>
        followed by the value element(s)."""
        extra = {}
        for label, key in EXTRA_LABELS.items():
            node = soup.find(lambda tag, label=label: tag.name == "div" and "font-bold" in (tag.get("class") or [])
                             and collapse_ws(tag.get_text()) == label)
            if node is None:
                continue
            values = [collapse_ws(sib.get_text(" ")) for sib in node.find_next_siblings()]
            values = [v for v in values if v]
            if values:
                extra[key] = " / ".join(values) if key == "education_listed" else values[0]
        return extra
