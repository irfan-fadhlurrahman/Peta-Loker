"""De-duplication: group postings that describe the same real job into one
vacancy, so demand isn't counted twice.

Three passes over job_postings:
  1. exact     — the same (source, source_job_id) is already one row (the
                 upsert key), so re-scrapes never duplicate;
  2. repost    — identical content_hash (title + company + location + the
                 start of the masked description) = the same job re-listed
                 under a new id;
  3. fuzzy     — candidates share a normalised company and province and were
                 posted within `window_days` of each other; a pair matches if
                 the titles are near-identical (token_set_ratio >= title_min)
                 AND the descriptions are similar (>= description_min), or the
                 titles are identical and both map to the same regency.
Union-find turns matching pairs into clusters.

Vacancy ids are stable across runs: a cluster keeps the oldest vacancy id any
of its postings already had, so classifications attached to it survive
re-clustering. Only when no member has an id does it get one (the canonical
posting's id — the earliest seen).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

from core.text import normalise_company, normalise_title


@dataclass
class PostingKey:
    posting_id: str
    source_id: str
    title: str
    company_key: str
    province_code: str | None
    region_code: str | None
    description: str
    content_hash: str | None
    day: date | None
    vacancy_id: str | None = None


class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for item in self.parent:
            out[self.find(item)].append(item)
        return out


def is_match(a: PostingKey, b: PostingKey, settings: dict) -> bool:
    title_a, title_b = normalise_title(a.title), normalise_title(b.title)
    if title_a == title_b and a.region_code and a.region_code == b.region_code and len(a.region_code) == 4:
        return True
    if fuzz.token_set_ratio(title_a, title_b) < settings["title_min_ratio"]:
        return False
    if not a.description or not b.description:
        return False
    return fuzz.token_set_ratio(a.description, b.description) >= settings["description_min_ratio"]


def candidate_pairs(keys: list[PostingKey], window_days: int):
    """Pairs that share company and province and were posted close together."""
    blocks: dict[tuple[str, str | None], list[PostingKey]] = defaultdict(list)
    for key in keys:
        if key.company_key:
            blocks[(key.company_key, key.province_code)].append(key)
    for block in blocks.values():
        block.sort(key=lambda k: k.day or date.min)
        for i, a in enumerate(block):
            for b in block[i + 1:]:
                if a.day and b.day and (b.day - a.day).days > window_days:
                    break
                yield a, b


def cluster(keys: list[PostingKey], settings: dict) -> UnionFind:
    uf = UnionFind([k.posting_id for k in keys])
    by_hash: dict[str, str] = {}
    for key in keys:  # pass 2: identical content
        if key.content_hash:
            if key.content_hash in by_hash:
                uf.union(by_hash[key.content_hash], key.posting_id)
            else:
                by_hash[key.content_hash] = key.posting_id
    for a, b in candidate_pairs(keys, settings["window_days"]):  # pass 3: fuzzy
        if uf.find(a.posting_id) != uf.find(b.posting_id) and is_match(a, b, settings):
            uf.union(a.posting_id, b.posting_id)
    return uf


def make_key(row, description_chars: int) -> PostingKey:
    day_text = (row["posted_at"] or row["first_fetched_at"] or "")[:10]
    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        day = None
    return PostingKey(
        posting_id=row["posting_id"],
        source_id=row["source_id"],
        title=row["title"] or "",
        company_key=normalise_company(row["company_name"]),
        province_code=row["province_code"],
        region_code=row["region_code"],
        description=normalise_title((row["description_masked"] or "")[:description_chars]),
        content_hash=row["content_hash"],
        day=day,
        vacancy_id=row["vacancy_id"],
    )
