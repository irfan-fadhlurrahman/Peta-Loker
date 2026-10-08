"""Small text helpers shared by ingestion, normalisation and dedup."""

from __future__ import annotations

import html
import re
import unicodedata

from bs4 import BeautifulSoup

_WS = re.compile(r"\s+")
_COMPANY_SUFFIXES = re.compile(
    r"\b(pt|cv|tbk|persero|perseroan terbatas|ud|pd|koperasi|yayasan|ltd|inc|co|corp|group|grup|indonesia|id)\b"
)
_NON_ALNUM = re.compile(r"[^0-9a-z ]+")


def collapse_ws(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def html_to_text(value: str | None) -> str:
    """Job descriptions often arrive as HTML (JSON-LD `description`, embedded
    JSON). Keep line breaks between blocks so later regexes don't glue
    sentences together, then collapse the rest."""
    if not value:
        return ""
    if "<" not in value:
        return collapse_ws(html.unescape(value))
    soup = BeautifulSoup(value, "lxml")
    for br in soup.find_all(["br", "p", "li", "div", "h1", "h2", "h3", "h4", "tr"]):
        br.append("\n")
    lines = (collapse_ws(line) for line in soup.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def normalise_company(name: str | None) -> str:
    """Canonical company key for hashing and dedup: 'PT. ABC Indonesia Tbk'
    and 'ABC' both become 'abc'. Falls back to the folded full name if
    stripping legal suffixes would leave nothing (a company literally named
    'Indonesia Group')."""
    if not name:
        return ""
    folded = _NON_ALNUM.sub(" ", ascii_fold(name).lower())
    stripped = collapse_ws(_COMPANY_SUFFIXES.sub(" ", folded))
    return stripped or collapse_ws(folded)


def normalise_title(title: str | None) -> str:
    """Lowercased, accent-folded, punctuation-free title for matching."""
    return collapse_ws(_NON_ALNUM.sub(" ", ascii_fold(title or "").lower()))


def truncate(text: str | None, limit: int) -> str:
    """Cut at a word boundary and add an ellipsis if anything was dropped."""
    text = collapse_ws(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:-") + "…"
