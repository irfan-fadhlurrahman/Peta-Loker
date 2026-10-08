"""Re-parse stored raw pages without fetching anything — after a parser fix,
or to rebuild job_postings from the raw store.

Reads every page key for a source (optionally one day) from the raw store,
runs the source's parse(), and upserts the result through the same masking
and age window as a live run.

Usage:
    uv run python scripts/reparse.py dealls [--day 2026-10-08]
    uv run python scripts/reparse.py all
"""

from __future__ import annotations

import argparse
import logging
import sys

from core import config, db
from core.base import posting_to_row
from core.errors import SourceError
from core.raw_store import get_raw_store
from core.timeutil import days_ago, to_iso
from scripts.run import load_class

logger = logging.getLogger("reparse")


def reparse_source(conn, name: str, day: str | None = None) -> dict[str, int]:
    settings = config.source(name)
    source = load_class(settings["class"])(conn, settings=settings)
    store = get_raw_store()
    counts = {"pages": 0, "saved": 0, "skipped": 0, "errors": 0}
    try:
        for key in store.list_pages(name, day):
            counts["pages"] += 1
            meta = store.load_meta(key)
            try:
                postings = source.parse(store.load(key), meta["url"])
            except SourceError as e:
                counts["errors"] += 1
                logger.warning("%s: %s", key, e)
                continue
            rows = []
            for posting in postings:
                age = days_ago(to_iso(posting.posted_at))
                if age is not None and age > source.max_age_days:
                    counts["skipped"] += 1
                    continue
                rows.append(posting_to_row(posting, key, meta["fetched_at"]))
            for row in rows:
                if row["company_hmac"]:
                    db.upsert_company(conn, row["company_hmac"], row["company_name"])
            counts["saved"] += db.upsert_postings(conn, rows)
    finally:
        source.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source")
    parser.add_argument("--day", help="only pages stored on this day (YYYY-MM-DD)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = db.connect()
    names = config.enabled_sources() if args.source == "all" else [args.source]
    for name in names:
        logger.info("%s: %s", name, reparse_source(conn, name, args.day))
    return 0


if __name__ == "__main__":
    sys.exit(main())
