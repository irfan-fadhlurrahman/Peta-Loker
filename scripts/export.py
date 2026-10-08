"""Export vacancies to CSV under data/export/ (gitignored).

By default the export uses the public rules (pseudonymised companies, hashed
ids, truncated descriptions, no URLs). --local exports every active vacancy
with company names and URLs, for your own analysis only.

Usage:
    uv run python scripts/export.py [--local]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from core import db
from scripts.generate_dashboard_data import build

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "export"


def flatten(record: dict) -> dict:
    kbji, region, salary, kbli = (record.get(k) or {} for k in ("kbji", "region", "salary", "kbli"))
    row = {
        "id": record["id"], "title": record["title"], "company": record.get("company"),
        "kbji_code": kbji.get("code"), "kbji_title": kbji.get("title"), "kbji_unit": kbji.get("unit"),
        "kbji_confidence": kbji.get("confidence"), "needs_review": kbji.get("needs_review"),
        "kbli_section": kbli.get("section"), "region_code": region.get("code"), "region": region.get("name"),
        "province": region.get("province"), "remote": record["remote"], "salary_min": salary.get("min"),
        "salary_max": salary.get("max"), "education": record["education"],
        "experience_years": record["experience_years"], "employment_type": record["employment_type"],
        "n_sources": record["n_sources"], "first_seen": record["first_seen"], "last_seen": record["last_seen"],
        "skills": "; ".join(record.get("skills") or []), "description": record["description"],
    }
    for key in ("company_name", "url", "source"):
        if key in record:
            row[key] = record[key]
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", action="store_true", help="include company names and URLs (never share)")
    args = parser.parse_args(argv)
    files = build(db.connect(), public=not args.local, limit=None if args.local else 0)
    rows = [flatten(r) for r in files["vacancies"]["vacancies"]]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / ("vacancies_local.csv" if args.local else "vacancies_public.csv")
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["id"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} vacancies to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
