"""Data quality gate. Runs before the dashboard is generated; a `fail` check
blocks publishing (exit code 1), a `warn` is reported but doesn't block.

    fail  title or location empty in more than 1% of postings
    fail  a classification uses a KBJI code that isn't in the reference table
    fail  an e-mail address, phone number or contact link in any clean text
          column (the masking step should have removed them all)
    warn  an enabled source produced no rows in its latest run
    warn  fewer than 90% of postings mapped to a BPS region
    warn  fewer than 35% of postings with a parsed salary
    warn  duplicate ratio outside 5%-60% (dedup too lax or too eager)

The JSON report is printed as the last line and saved to
data/quality/last.json, which the deploy check reads.

Usage:
    uv run python scripts/quality_check.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core import config, db
from core.masking import find_pii
from core.timeutil import now_iso

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "data" / "quality" / "last.json"
PII_COLUMNS = ("title", "description_masked", "location_raw", "salary_raw", "education_raw", "experience_raw")


def _ratio(n: int, total: int) -> float:
    return round(n / total, 4) if total else 0.0


def run_checks(conn, settings: dict | None = None, sources: list[str] | None = None) -> dict:
    """`sources`: which sources must have produced rows (default: every
    enabled source in config; the demo passes just its file source)."""
    settings = settings or config.quality()
    checks = []

    def add(name: str, level: str, ok: bool, value, kind: str):
        checks.append({"check": name, "level": level, "status": "pass" if ok else level, "value": value,
                       "kind": kind})

    total = db.count(conn, "job_postings")
    empty = db.count(conn, "job_postings", "title IS NULL OR title = '' OR location_raw IS NULL OR location_raw = ''")
    add("title_and_location_present", "fail",
        _ratio(empty, total) <= settings["max_empty_title_location_ratio"], _ratio(total - empty, total), "ratio")

    invalid = conn.execute(
        """
        SELECT COUNT(*) FROM job_classifications c
        WHERE (c.kbji4 IS NOT NULL AND c.kbji4 NOT IN (SELECT code FROM ref_kbji WHERE level = 4))
           OR (c.kbji_code IS NOT NULL AND c.kbji_code NOT IN (SELECT code FROM ref_kbji WHERE level = 5))
        """
    ).fetchone()[0]
    add("kbji_codes_valid", "fail", invalid == 0, invalid, "count")

    pii_hits = 0
    for row in conn.execute(f"SELECT {', '.join(PII_COLUMNS)} FROM job_postings"):
        pii_hits += sum(1 for col in PII_COLUMNS if find_pii(row[col]))
    add("no_contact_details_in_clean_tables", "fail", pii_hits == 0, pii_hits, "count")

    silent = []
    for source in sources if sources is not None else config.enabled_sources():
        last = conn.execute(
            "SELECT status, n_saved, n_skipped FROM job_run_logs WHERE source_id = ? ORDER BY started_at DESC LIMIT 1",
            (source,),
        ).fetchone()
        if last is None or (last["n_saved"] == 0 and last["n_skipped"] == 0):
            silent.append(source)
    add("every_source_produced_rows", "warn", not silent, silent, "list")

    mapped = db.count(conn, "job_postings", "region_code IS NOT NULL OR is_remote = 1")
    add("region_mapped", "warn", _ratio(mapped, total) >= settings["min_region_mapped_ratio"], _ratio(mapped, total),
        "ratio")

    salary = db.count(conn, "job_postings", "salary_min IS NOT NULL")
    add("salary_parsed", "warn", _ratio(salary, total) >= settings["min_salary_parsed_ratio"], _ratio(salary, total),
        "ratio")

    vacancies = db.count(conn, "job_vacancies")
    dup_ratio = _ratio(total - vacancies, total) if vacancies else 0.0
    lo, hi = settings["duplicate_ratio_range"]
    add("duplicate_ratio_in_range", "warn", lo <= dup_ratio <= hi, dup_ratio, "ratio")

    failed = [c["check"] for c in checks if c["status"] == "fail"]
    warned = [c["check"] for c in checks if c["status"] == "warn"]
    return {"status": "fail" if failed else "pass", "fail": failed, "warn": warned, "postings": total,
            "vacancies": vacancies, "checked_at": now_iso(), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    report = run_checks(db.connect())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for check in report["checks"]:
        print(f"{check['status']:>5}  {check['check']}: {check['value']}")
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, ensure_ascii=False))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
