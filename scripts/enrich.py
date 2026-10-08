"""Code vacancies to KBJI 2026 (two steps), extract skills, and code
companies to a KBLI 2020 section — with BytePlus Ark (Seed 2.0 Pro).

Only vacancies without a successful classification for the current model and
PROMPT_VERSION are sent, so re-runs cost nothing and a new prompt version
adds a fresh set of rows instead of overwriting the old ones.

Usage:
    uv run python scripts/enrich.py                     # everything pending
    uv run python scripts/enrich.py --limit 200         # newest 200 vacancies
    uv run python scripts/enrich.py --vacancies ids.txt # e.g. the evaluation set
    uv run python scripts/enrich.py --dry-run           # build prompts, estimate tokens, call nothing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from collections import defaultdict
from pathlib import Path

from core import config, db
from core.classify import (
    PROMPT_VERSION,
    CallRecord,
    Taxonomy,
    items_prompt,
    kbli_system,
    run_batch,
    step_a_system,
    step_b_system,
    validate_kbli,
    validate_step_a,
    validate_step_b,
)
from core.llm_client import LlmClient
from core.text import collapse_ws, truncate
from core.timeutil import now_iso

logger = logging.getLogger("enrich")
CHARS_PER_TOKEN = 3.5  # rough, for --dry-run estimates of Indonesian text


def vacancy_item(ref: str, row, description_chars: int) -> dict:
    extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
    item = {"ref": ref, "title": row["title"]}
    for key, label in (("job_level", "level"), ("job_function", "function"), ("job_role", "role"),
                       ("occupationalCategory", "category")):
        if extra.get(key):
            item[label] = extra[key]
    if row["education_raw"]:
        item["education"] = row["education_raw"]
    item["description"] = truncate(collapse_ws(row["description_masked"]), description_chars)
    return item


def normalise_skill(skill: str) -> str:
    return collapse_ws(skill).lower().strip(" .;:-")


def chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class Recorder:
    """Writes one llm_usage row per call and keeps totals."""

    def __init__(self, conn, model: str):
        self.conn, self.model = conn, model
        self.totals = defaultdict(int)

    def __call__(self, call: CallRecord) -> None:
        r = call.response
        db.log_llm_call(self.conn, {
            "call_id": uuid.uuid4().hex, "step": call.step, "model": self.model, "prompt_version": PROMPT_VERSION,
            "n_items": call.n_items, "input_tokens": r.input_tokens if r else None,
            "output_tokens": r.output_tokens if r else None, "latency_ms": r.latency_ms if r else None,
            "status": call.status, "date_created": now_iso(),
        })
        self.totals["calls"] += 1
        self.totals[f"calls_{call.status}"] += 1
        if r:
            self.totals["input_tokens"] += r.input_tokens or 0
            self.totals["output_tokens"] += r.output_tokens or 0


def classify_vacancies(conn, client: LlmClient, tax: Taxonomy, rows, settings: dict, record: Recorder) -> dict:
    batch_size, max_skills = settings["batch_size"], settings["max_skills"]
    threshold = settings["review_threshold"]
    items = {f"j{i}": (row, vacancy_item(f"j{i}", row, settings["description_chars"])) for i, row in enumerate(rows, 1)}

    # Step A — 4-digit unit group, skills, PII flag.
    system_a = step_a_system(tax, max_skills)
    step_a, failed_a = {}, []
    for chunk in chunks(list(items), batch_size):
        results, bad = run_batch(client, "kbji4", system_a, [items[r][1] for r in chunk],
                                 lambda refs, res: validate_step_a(refs, res, tax, max_skills), record)
        step_a.update(results)
        failed_a.extend(bad)

    # Step B — 6-digit jabatan within each unit group.
    by_unit = defaultdict(list)
    for ref, result in step_a.items():
        by_unit[result["kbji4"]].append(ref)
    step_b = {}
    for unit, refs in by_unit.items():
        system_b = step_b_system(tax, unit)
        for chunk in chunks(refs, batch_size):
            batch = [{"ref": r, "title": items[r][1]["title"], "description": items[r][1]["description"]}
                     for r in chunk]
            results, _ = run_batch(client, "kbji6", system_b, batch,
                                   lambda refs, res, unit=unit: validate_step_b(refs, res, tax, unit), record)
            step_b.update(results)

    now, model = now_iso(), client.model
    out_rows = []
    for ref, (row, _) in items.items():
        a, b = step_a.get(ref), step_b.get(ref)
        status = "ok" if a and b else ("partial" if a else "failed")
        confidences = [c for c in ((a or {}).get("confidence4"), (b or {}).get("confidence")) if c is not None]
        out_rows.append({
            "vacancy_id": row["vacancy_id"], "model": model, "prompt_version": PROMPT_VERSION,
            "kbji4": a["kbji4"] if a else None, "alt4": a["alt4"] if a else None,
            "confidence4": a["confidence4"] if a else None, "kbji_code": b["kbji_code"] if b else None,
            "confidence": b["confidence"] if b else None, "reason": b["reason"] if b else None,
            "needs_review": int(status != "ok" or min(confidences, default=0) < threshold
                                or bool(a and a["pii_found"])),
            "pii_found": int(bool(a and a["pii_found"])), "status": status, "date_created": now,
        })
        if a:
            skills = {normalise_skill(s): s for s in a["skills"] if normalise_skill(s)}
            db.save_skills(conn, row["vacancy_id"], list(skills.items()), model, PROMPT_VERSION)
    db.save_classifications(conn, out_rows)
    return {"vacancies": len(out_rows), "ok": sum(r["status"] == "ok" for r in out_rows),
            "partial": sum(r["status"] == "partial" for r in out_rows),
            "failed": sum(r["status"] == "failed" for r in out_rows),
            "needs_review": sum(r["needs_review"] for r in out_rows)}


def classify_companies(conn, client: LlmClient, tax: Taxonomy, settings: dict, record: Recorder,
                       limit: int | None = None) -> dict:
    companies = db.companies_to_code(conn, limit)
    system = kbli_system(tax)
    coded = []
    for chunk in chunks(companies, settings["batch_size"] * 2):
        batch = [{"ref": f"c{i}", "company": c["name"], "industry_hint": c["industry_hint"], "jobs": c["titles"]}
                 for i, c in enumerate(chunk, 1)]
        results, _ = run_batch(client, "kbli", system, batch, lambda refs, res: validate_kbli(refs, res, tax), record)
        for i, company in enumerate(chunk, 1):
            if f"c{i}" in results:
                coded.append({"company_hmac": company["company_hmac"], "model": client.model,
                              "prompt_version": PROMPT_VERSION, "kbli_section": results[f"c{i}"]["kbli_section"]})
    db.save_company_kbli(conn, coded)
    return {"companies": len(companies), "coded": len(coded)}


def dry_run(conn, tax: Taxonomy, rows, settings: dict) -> dict:
    """Token estimate for the pending work, without calling the API."""
    batch_size = settings["batch_size"]
    items = [vacancy_item(f"j{i}", r, settings["description_chars"]) for i, r in enumerate(rows, 1)]
    system_a = step_a_system(tax, settings["max_skills"])
    n_batches = -(-len(items) // batch_size) if items else 0
    a_in = n_batches * len(system_a) + sum(len(items_prompt([it])) for it in items)
    b_in = sum(len(step_b_system(tax, "2511")) + len(items_prompt([it])) // 2 for it in items) / batch_size * 1.5
    est_in = (a_in + b_in) / CHARS_PER_TOKEN
    est_out = len(items) * 130
    return {"vacancies": len(items), "batches_step_a": n_batches, "system_prompt_tokens_step_a":
            int(len(system_a) / CHARS_PER_TOKEN), "estimated_input_tokens": int(est_in),
            "estimated_output_tokens": est_out}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, help="at most this many vacancies (newest first)")
    parser.add_argument("--vacancies", type=Path, help="file with one vacancy_id per line")
    parser.add_argument("--skip-kbli", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="estimate tokens without calling the API")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings, tax, conn = config.llm(), Taxonomy.from_reference(), db.connect()
    ids = None
    if args.vacancies:
        ids = [line.strip() for line in args.vacancies.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.dry_run:
        import os
        model = os.environ.get("ARK_MODEL") or "dry-run"
        rows = db.vacancies_to_classify(conn, model, PROMPT_VERSION, args.limit, ids)
        logger.info("dry run: %s", dry_run(conn, tax, rows, settings))
        return 0

    client = LlmClient(thinking=config.llm().get("thinking"))
    record = Recorder(conn, client.model)
    try:
        rows = db.vacancies_to_classify(conn, client.model, PROMPT_VERSION, args.limit, ids)
        logger.info("classifying %d vacancies with %s (prompt %s)", len(rows), client.model, PROMPT_VERSION)
        logger.info("vacancies: %s", classify_vacancies(conn, client, tax, rows, settings, record))
        if not args.skip_kbli:
            logger.info("companies: %s", classify_companies(conn, client, tax, settings, record))
    finally:
        client.close()
    logger.info("LLM usage: %s", dict(record.totals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
