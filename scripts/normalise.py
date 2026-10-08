"""Normalise every posting: monthly IDR salary, education level, years of
experience, employment type and BPS region codes (core/normalise.py).

Idempotent — values are always re-derived from the raw text columns
(salary_raw, education_raw, …), never from earlier normalised values, so it
can be re-run after a rule change.

Usage:
    uv run python scripts/normalise.py [--only-new]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter

from core import db
from core.normalise import RegionIndex, parse_education, parse_employment, parse_experience, parse_salary
from core.timeutil import now_iso

logger = logging.getLogger("normalise")


def normalise_row(row, regions: RegionIndex, now: str) -> dict:
    extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
    salary_min, salary_max, period = parse_salary(row["salary_raw"])
    # Some sites put the education list only in the HTML (Loker.id).
    education = parse_education(row["education_raw"]) or parse_education(extra.get("education_listed"))
    location = regions.refine(regions.resolve(row["location_raw"]), extra.get("street_address"))
    return {
        "posting_id": row["posting_id"],
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_period": period,
        "education_level": education,
        "experience_years": parse_experience(row["experience_raw"]),
        "employment_type": parse_employment(row["employment_type_raw"]),
        "region_code": location.region_code,
        "province_code": location.province_code,
        "is_remote": int(location.is_remote or bool(extra.get("remote"))),
        "normalised_at": now,
    }


def run(conn, only_new: bool = False) -> dict[str, float]:
    regions = RegionIndex.from_reference()
    now = now_iso()
    rows = [normalise_row(r, regions, now) for r in db.postings_to_normalise(conn, only_new)]
    db.update_normalised(conn, rows)
    total = len(rows) or 1
    filled = Counter()
    for r in rows:
        for key in ("salary_min", "education_level", "experience_years", "employment_type", "region_code"):
            filled[key] += r[key] is not None
    coverage = {k: round(v / total, 3) for k, v in filled.items()}
    regency = sum(1 for r in rows if r["region_code"] and len(r["region_code"]) == 4)
    coverage["regency_level"] = round(regency / total, 3)
    return {"postings": len(rows), **coverage}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only-new", action="store_true", help="only postings fetched since their last normalisation")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("normalised: %s", run(db.connect(), args.only_new))
    return 0


if __name__ == "__main__":
    sys.exit(main())
