"""Collect postings from one source or all enabled sources.

Each source is its JobSource subclass from config/sources.yaml (`class:`),
run through the shared template method (robots, delay, page cap, raw store,
masking, 60-day window, upsert, run log).

Usage:
    uv run python scripts/run.py all
    uv run python scripts/run.py dealls --max-pages 20

Prints a RUN_SUMMARY JSON line per source; exits 1 if any source failed or
was blocked.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys

from core import config, db

SUMMARY_PREFIX = "RUN_SUMMARY "


def load_class(dotted: str):
    module_name, _, class_name = dotted.rpartition(".")
    return getattr(importlib.import_module(module_name), class_name)


def run_source(conn, name: str, max_pages: int | None = None):
    settings = config.source(name)
    source_cls = load_class(settings["class"])
    source = source_cls(conn, settings=settings, max_pages=max_pages)
    try:
        return source.run()
    finally:
        source.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="source name from config/sources.yaml, or 'all' (enabled sources)")
    parser.add_argument("--max-pages", type=int, help="override the per-source page cap for this run")
    args = parser.parse_args(argv)
    # Line-buffer stdout/stderr so a redirected log (scheduled runs) fills in
    # as the run goes, not only when the process exits.
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    names = config.enabled_sources() if args.source == "all" else [args.source]
    conn = db.connect()
    db.init_schema(conn)
    failed = False
    for name in names:
        result = run_source(conn, name, args.max_pages)
        print(SUMMARY_PREFIX + json.dumps({"source": name, "status": result.status, "error_type": result.error_type,
                                           **result.counts}), flush=True)
        failed |= result.status in ("failed", "blocked")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
