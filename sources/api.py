"""ApiSource: sources whose job data arrives as JSON — either from a public
endpoint the site's own pages call, or embedded in the page itself (Next.js
__NEXT_DATA__). One fetched payload often carries many postings, so a single
request can replace a dozen detail-page downloads.

Subclasses yield payload URLs from list_jobs() and turn a decoded payload into
postings in parse_payload().
"""

from __future__ import annotations

import json
from abc import abstractmethod

from bs4 import BeautifulSoup

from core.base import JobPosting, JobSource
from core.errors import LayoutChanged


def load_json(raw: str, url: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LayoutChanged(f"{url} did not return JSON: {e}") from e


def next_data(raw: str, url: str) -> dict:
    """The JSON a Next.js page embeds in <script id="__NEXT_DATA__">."""
    tag = BeautifulSoup(raw, "lxml").find("script", id="__NEXT_DATA__")
    if tag is None or not (tag.string or "").strip():
        raise LayoutChanged(f"no __NEXT_DATA__ on {url}")
    return load_json(tag.string, url)


class ApiSource(JobSource):
    """parse() decodes the payload as JSON, or the page's embedded
    __NEXT_DATA__ when `embedded = True`, then hands it to parse_payload()."""

    embedded: bool = False

    def parse(self, raw: str, url: str) -> list[JobPosting]:
        data = next_data(raw, url) if self.embedded else load_json(raw, url)
        return self.parse_payload(data, url)

    @abstractmethod
    def parse_payload(self, data, url: str) -> list[JobPosting]:
        """Turn one decoded payload into postings. Raise LayoutChanged if the
        expected keys are missing."""
