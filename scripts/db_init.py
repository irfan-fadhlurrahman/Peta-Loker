"""Create the database (schema + reference data). Safe to re-run: tables are
created only if missing and reference rows are upserted.

Reference data comes from the committed CSVs under reference/, so a fresh
clone needs no network access. Rebuild those CSVs from their public sources
with `make reference`.

Usage:
    uv run python scripts/db_init.py [--db path]
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from core import config, db

REPO_ROOT = Path(__file__).resolve().parent.parent
KBJI_CSV = REPO_ROOT / "reference" / "kbji_2026.csv"
REGIONS_CSV = REPO_ROOT / "reference" / "regions.csv"

logger = logging.getLogger("db_init")


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(f)]


def load_reference(conn) -> dict[str, int]:
    kbji = _read_csv(KBJI_CSV)
    for row in kbji:
        row["level"] = int(row["level"])
    return {
        "ref_kbji": db.upsert_ref_kbji(conn, kbji),
        "ref_regions": db.upsert_ref_regions(conn, _read_csv(REGIONS_CSV)),
    }


def register_sources(conn) -> int:
    for name, settings in config.sources().items():
        db.upsert_source(conn, name, settings["class"], settings.get("base_url"), settings.get("enabled", True))
    return len(config.sources())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="database path (default: DB_PATH or data/peta_loker.db)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    conn = db.connect(args.db)
    db.init_schema(conn)
    loaded = load_reference(conn)
    n_sources = register_sources(conn)
    logger.info("schema ready at %s; loaded %s; %d sources registered", args.db or db.db_path(), loaded, n_sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
