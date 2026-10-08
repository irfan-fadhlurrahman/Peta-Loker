"""`make demo`: the whole pipeline on the synthetic sample, with no network
and no accounts — FileSource → normalise → dedup → classification → quality
gate → dashboard JSON. Then `make dashboard-serve` shows the result.

It uses its own database (data/demo.db) and the local raw store, so it never
touches real collected data. One step is substituted: KBJI/KBLI codes for
the 30 synthetic job titles come from hand-assigned labels
(data/sample/kbji_labels.csv) instead of an LLM call, so the demo is free
and deterministic. Pass --llm to use the real BytePlus Ark coder instead
(needs ARK_* in .env).

Usage:
    uv run python scripts/demo.py [--llm]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = REPO_ROOT / "data" / "demo.db"
LABELS = REPO_ROOT / "data" / "sample" / "kbji_labels.csv"
OUT_DIR = REPO_ROOT / "dashboard" / "data"
DEMO_MODEL, DEMO_PROMPT = "demo-hand-labels", "demo"


def _prepare_env() -> None:
    # Before any core module reads them.
    os.environ["DB_PATH"] = str(DEMO_DB)
    os.environ["RAW_STORE"] = "local"
    os.environ.setdefault("HMAC_SECRET", "demo-only-secret")


def apply_hand_labels(conn) -> dict[str, int]:
    """Classify demo vacancies from the hand-assigned label table."""
    from core import db
    from core.timeutil import now_iso

    with LABELS.open(encoding="utf-8", newline="") as f:
        labels = {row["title"]: row for row in csv.DictReader(f)}
    rows = conn.execute(
        """
        SELECT v.vacancy_id, p.title, p.company_hmac FROM job_vacancies v
        JOIN job_postings p ON p.posting_id = v.canonical_posting_id
        """
    ).fetchall()
    now, out, company_sections = now_iso(), [], {}
    for row in rows:
        label = labels.get(row["title"])
        if label is None:
            continue
        confidence = float(label["confidence"])
        code = label["kbji_code"]
        out.append({"vacancy_id": row["vacancy_id"], "model": DEMO_MODEL, "prompt_version": DEMO_PROMPT,
                    "kbji4": code[:4], "alt4": None, "confidence4": confidence, "kbji_code": code,
                    "confidence": confidence, "reason": "Label tangan untuk data contoh (bukan keluaran LLM).",
                    "needs_review": int(confidence < 0.7), "pii_found": 0, "status": "ok", "date_created": now})
        skills = [s.strip() for s in label["skills"].split(";") if s.strip()]
        db.save_skills(conn, row["vacancy_id"], [(s.lower(), s) for s in skills], DEMO_MODEL, DEMO_PROMPT)
        if row["company_hmac"]:
            company_sections.setdefault(row["company_hmac"], Counter())[label["kbli_section"]] += 1
    db.save_classifications(conn, out)
    db.save_company_kbli(conn, [
        {"company_hmac": h, "kbli_section": c.most_common(1)[0][0], "model": DEMO_MODEL, "prompt_version": DEMO_PROMPT}
        for h, c in company_sections.items()
    ])
    return {"classified": len(out), "companies": len(company_sections)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llm", action="store_true", help="code with BytePlus Ark instead of the hand labels")
    args = parser.parse_args(argv)
    _prepare_env()
    started = time.monotonic()

    from core import db
    from scripts import dedup, normalise, quality_check
    from scripts.db_init import load_reference, register_sources
    from scripts.generate_dashboard_data import build
    from scripts.run import run_source

    for suffix in ("", "-wal", "-shm"):
        Path(f"{DEMO_DB}{suffix}").unlink(missing_ok=True)
    conn = db.connect()
    db.init_schema(conn)
    load_reference(conn)
    register_sources(conn)

    steps = {}
    result = run_source(conn, "sample")
    steps["collect"] = {"status": result.status, **{k: v for k, v in result.counts.items() if v}}
    steps["normalise"] = normalise.run(conn)
    steps["dedup"] = dedup.run(conn)
    if args.llm:
        from core import config
        from core.classify import PROMPT_VERSION, Taxonomy
        from core.llm_client import LlmClient
        from scripts.enrich import Recorder, classify_companies, classify_vacancies

        client = LlmClient()
        record = Recorder(conn, client.model)
        tax = Taxonomy.from_reference()
        rows = db.vacancies_to_classify(conn, client.model, PROMPT_VERSION)
        steps["classify"] = classify_vacancies(conn, client, tax, rows, config.llm(), record)
        steps["kbli"] = classify_companies(conn, client, tax, config.llm(), record)
        client.close()
    else:
        steps["classify"] = apply_hand_labels(conn)

    report = quality_check.run_checks(conn, sources=["sample"])
    quality_check.REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    quality_check.REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    steps["quality"] = {"status": report["status"], "warn": report["warn"]}

    files = build(conn, public=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (OUT_DIR / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    for name, value in steps.items():
        print(f"{name:>10}: {value}")
    print(f"\nDemo built in {time.monotonic() - started:.1f}s. Dashboard data in {OUT_DIR}.")
    print("Open it with:  make dashboard-serve   then visit http://localhost:8000")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
