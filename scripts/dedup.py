"""Group postings into vacancies (core/dedup.py) and refresh job_vacancies.

Run after normalise (dedup blocks on province_code).

Tuning the thresholds against hand-checked pairs:
    uv run python scripts/dedup.py --review 100      # writes data/review/dedup_pairs.csv
    # fill the `label` column with 1 (same job) or 0 (different job)
    uv run python scripts/dedup.py --score data/review/dedup_pairs.csv

The review file holds real (masked) postings, so it stays under data/
(gitignored).

Usage:
    uv run python scripts/dedup.py
"""

from __future__ import annotations

import argparse
import csv
import logging
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from rapidfuzz import fuzz

from core import config, db
from core.dedup import candidate_pairs, cluster, is_match, make_key
from core.text import normalise_title
from core.timeutil import now_iso, today_jakarta

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEW_PATH = REPO_ROOT / "data" / "review" / "dedup_pairs.csv"

logger = logging.getLogger("dedup")


def run(conn, settings: dict | None = None, today: date | None = None) -> dict[str, int]:
    settings = settings or config.dedup()
    today = today or today_jakarta()
    rows = db.postings_for_dedup(conn)
    keys = {r["posting_id"]: make_key(r, settings["description_chars"]) for r in rows}
    groups = cluster(list(keys.values()), settings).groups()
    by_id = {r["posting_id"]: r for r in rows}

    vacancies, assignments, claimed = [], {}, set()
    inactive_before = today - timedelta(days=settings["inactive_after_days"])
    for members in groups.values():
        members.sort(key=lambda pid: (keys[pid].day or date.max, pid))
    # Keep the oldest vacancy id a group's members already had, so classifications
    # survive re-clustering, but let each id go to one group only: when a cluster
    # splits, the other part gets a fresh id instead of staying merged under the old one.
    for members in sorted(groups.values(), key=lambda ms: (-len(ms), ms[0])):
        existing = sorted({keys[m].vacancy_id for m in members if keys[m].vacancy_id})
        vacancy_id = next((v for v in existing if v not in claimed), None)
        if vacancy_id is None:
            vacancy_id = next((m for m in members if m not in claimed), None)
        suffix = 1
        while vacancy_id is None or vacancy_id in claimed:  # every candidate taken
            vacancy_id, suffix = f"{members[0]}~{suffix}", suffix + 1
        claimed.add(vacancy_id)
        member_rows = [by_id[m] for m in members]
        first_seen = min((keys[m].day for m in members if keys[m].day), default=None)
        last_seen = max(r["fetched_at"][:10] for r in member_rows)
        valid = [r["valid_through"][:10] for r in member_rows if r["valid_through"]]
        expired = bool(valid) and max(valid) < today.isoformat()
        vacancies.append({
            "vacancy_id": vacancy_id,
            "canonical_posting_id": members[0],
            "first_seen": (first_seen or today).isoformat(),
            "last_seen": last_seen,
            "n_postings": len(members),
            "n_sources": len({r["source_id"] for r in member_rows}),
            "is_active": int(last_seen >= inactive_before.isoformat() and not expired),
            "date_modified": now_iso(),
        })
        for m in members:
            assignments[m] = vacancy_id
    db.save_vacancies(conn, vacancies, assignments)
    merged = len(rows) - len(vacancies)
    return {
        "postings": len(rows),
        "vacancies": len(vacancies),
        "merged": merged,
        "cross_source": sum(1 for v in vacancies if v["n_sources"] > 1),
        "active": sum(v["is_active"] for v in vacancies),
    }


def write_review(conn, n: int, path: Path = REVIEW_PATH, seed: int = 7) -> int:
    """A labelling sheet of candidate pairs: half the model calls a match,
    half it doesn't but that share company and province (the hard cases)."""
    settings = config.dedup()
    keys = [make_key(r, settings["description_chars"]) for r in db.postings_for_dedup(conn)]
    pairs = list(candidate_pairs(keys, settings["window_days"]))
    pairs = [(a, b) for a, b in pairs if a.content_hash != b.content_hash]  # exact reposts aren't interesting
    rng = random.Random(seed)
    matched = [p for p in pairs if is_match(*p, settings)]
    unmatched = [p for p in pairs if not is_match(*p, settings)]
    unmatched.sort(key=lambda p: -fuzz.token_set_ratio(normalise_title(p[0].title), normalise_title(p[1].title)))
    chosen = rng.sample(matched, min(len(matched), n // 2)) + unmatched[: n - min(len(matched), n // 2)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["pair_id", "predicted", "label", "a_id", "a_title", "a_region", "a_text",
                         "b_id", "b_title", "b_region", "b_text"])
        for i, (a, b) in enumerate(chosen, 1):
            writer.writerow([i, int(is_match(a, b, settings)), "", a.posting_id, a.title, a.region_code,
                             a.description[:300], b.posting_id, b.title, b.region_code, b.description[:300]])
    return len(chosen)


def score(path: Path, conn=None) -> dict[str, float]:
    """Precision/recall of the labelled pairs. With `conn`, "predicted" is the
    current clustering (both postings in the same vacancy, chains included), so
    a rule change can be re-scored without a new sheet; otherwise the sheet's
    own `predicted` column (the rule at the time it was written)."""
    vacancy_of = dict(conn.execute("SELECT posting_id, vacancy_id FROM job_postings").fetchall()) if conn else None
    counts = defaultdict(int)
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["label"].strip() not in ("0", "1"):
                continue
            if vacancy_of is None:
                predicted = int(row["predicted"])
            else:
                a, b = vacancy_of.get(row["a_id"]), vacancy_of.get(row["b_id"])
                predicted = int(a is not None and a == b)
            counts[(predicted, int(row["label"]))] += 1
    tp, fp, fn = counts[(1, 1)], counts[(1, 0)], counts[(0, 1)]
    labelled = sum(counts.values())
    return {
        "labelled_pairs": labelled,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall_on_sample": round(tp / (tp + fn), 3) if tp + fn else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--review", type=int, metavar="N", help="write N candidate pairs for hand labelling")
    parser.add_argument("--score", type=Path, metavar="CSV", help="precision/recall from a labelled review file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = db.connect()
    if args.score:
        logger.info("dedup score: %s", score(args.score, conn))
    elif args.review:
        logger.info("wrote %d pairs to %s", write_review(conn, args.review), REVIEW_PATH)
    else:
        logger.info("dedup: %s", run(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
