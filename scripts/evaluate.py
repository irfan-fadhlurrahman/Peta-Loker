"""Measure KBJI coding accuracy against hand labels, and write docs/results.md.

1. Make a labelling sheet — a sample stratified across sources and predicted
   major groups, so no single portal or occupation dominates:
       uv run python scripts/evaluate.py --sample 200
   -> data/review/kbji_labels.csv (real, masked postings; stays gitignored)
2. Fill `gold_kbji_code` with the correct 6-digit code (or 4-digit if you're
   only sure of the unit group); `scripts/label.py kbji` walks the sheet row by row.
   Leave a row blank to skip it.
3. Score and write the results page:
       uv run python scripts/evaluate.py --score data/review/kbji_labels.csv

docs/results.md contains only aggregate numbers — no posting text.

Metrics: accuracy at 1, 2, 4 and 6 digits (prefix agreement with the gold
code), top-2 at 4 digits (the second choice counts), accuracy above vs. below
the review threshold (is the confidence score meaningful?), review rate, and
LLM cost per 1,000 vacancies from the logged token counts.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from core import config, db
from core.timeutil import now_iso

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET = REPO_ROOT / "data" / "review" / "kbji_labels.csv"
RESULTS = REPO_ROOT / "docs" / "results.md"
DEDUP_SHEET = REPO_ROOT / "data" / "review" / "dedup_pairs.csv"


def _latest_classifications(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.*, p.source_id, p.title, p.extra_json, p.description_masked, k.title AS jabatan_title
        FROM job_classifications c
        JOIN job_vacancies v ON v.vacancy_id = c.vacancy_id
        JOIN job_postings p ON p.posting_id = v.canonical_posting_id
        LEFT JOIN ref_kbji k ON k.code = c.kbji_code
        WHERE c.status IN ('ok', 'partial')
        ORDER BY c.vacancy_id, c.date_created
        """
    ).fetchall()
    return list({r["vacancy_id"]: dict(r) for r in rows}.values())


def write_sheet(conn, n: int, path: Path = SHEET, seed: int = 11) -> int:
    rows = _latest_classifications(conn)
    strata = defaultdict(list)
    for r in rows:
        strata[(r["source_id"], (r["kbji4"] or "?")[0])].append(r)
    rng = random.Random(seed)
    for group in strata.values():
        rng.shuffle(group)
    chosen = []
    while len(chosen) < n and any(strata.values()):  # round-robin across strata
        for key in sorted(strata):
            if strata[key] and len(chosen) < n:
                chosen.append(strata[key].pop())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["vacancy_id", "source", "title", "level_function", "description", "predicted_kbji_code",
                         "predicted_title", "predicted_kbji4", "alt4", "confidence", "gold_kbji_code", "notes"])
        for r in chosen:
            extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
            level = " / ".join(str(extra[k]) for k in ("job_level", "job_function", "job_role") if extra.get(k))
            writer.writerow([r["vacancy_id"], r["source_id"], r["title"], level,
                             (r["description_masked"] or "")[:400], r["kbji_code"], r["jabatan_title"], r["kbji4"],
                             r["alt4"], r["confidence"] if r["confidence"] is not None else r["confidence4"], "", ""])
    return len(chosen)


def score_sheet(path: Path, threshold: float) -> dict:
    hits = defaultdict(int)
    n = 0
    above = [0, 0]
    below = [0, 0]
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            gold = (row.get("gold_kbji_code") or "").strip()
            if not gold:
                continue
            n += 1
            pred6 = row["predicted_kbji_code"] or ""
            pred4 = row["predicted_kbji4"] or pred6[:4]
            gold_digits = gold.replace(".", "")
            pred_digits = pred6.replace(".", "") or pred4
            for digits in (1, 2, 4):
                hits[digits] += pred_digits[:digits] == gold_digits[:digits]
            if len(gold_digits) == 6:
                hits["6_n"] += 1
                hits[6] += pred_digits == gold_digits
            hits["top2_4"] += gold_digits[:4] in (pred4, (row.get("alt4") or ""))
            correct4 = pred_digits[:4] == gold_digits[:4]
            try:
                conf = float(row.get("confidence") or 0)
            except ValueError:
                conf = 0.0
            bucket = above if conf >= threshold else below
            bucket[0] += 1
            bucket[1] += correct4
    pct = lambda k, d: round(hits[k] / d, 3) if d else None  # noqa: E731
    return {
        "labelled": n,
        "accuracy_1": pct(1, n), "accuracy_2": pct(2, n), "accuracy_4": pct(4, n),
        "accuracy_6": pct(6, hits["6_n"]), "labelled_6_digit": hits["6_n"],
        "top2_accuracy_4": pct("top2_4", n),
        "accuracy_4_confident": round(above[1] / above[0], 3) if above[0] else None, "n_confident": above[0],
        "accuracy_4_low_confidence": round(below[1] / below[0], 3) if below[0] else None, "n_low": below[0],
    }


def cost_per_thousand(conn) -> dict:
    prices = config.llm().get("price_usd_per_mtok", {})
    row = conn.execute(
        "SELECT SUM(input_tokens), SUM(output_tokens) FROM llm_usage WHERE step IN ('kbji4', 'kbji6', 'kbli')"
    ).fetchone()
    coded = conn.execute("SELECT COUNT(DISTINCT vacancy_id) FROM job_classifications WHERE status = 'ok'").fetchone()[0]
    tokens_in, tokens_out = row[0] or 0, row[1] or 0
    usd = tokens_in / 1e6 * prices.get("input", 0) + tokens_out / 1e6 * prices.get("output", 0)
    models = [r[0] for r in conn.execute("SELECT DISTINCT model FROM llm_usage ORDER BY model")]
    return {"models": models, "vacancies_coded": coded, "input_tokens": tokens_in, "output_tokens": tokens_out,
            "usd_total": round(usd, 4), "usd_per_1000": round(usd / coded * 1000, 3) if coded else None}


def write_results(conn, metrics: dict | None, path: Path = RESULTS) -> None:
    from scripts.dedup import score as dedup_score

    postings = db.count(conn, "job_postings")
    vacancies = db.count(conn, "job_vacancies")
    per_source = conn.execute("SELECT source_id, COUNT(*) FROM job_postings GROUP BY source_id ORDER BY 1").fetchall()
    filled = {k: db.count(conn, "job_postings", f"{k} IS NOT NULL") for k in
              ("salary_min", "education_level", "experience_years")}
    regency = db.count(conn, "job_postings", "length(region_code) = 4")
    review = db.count(conn, "job_classifications", "needs_review = 1")
    classified = db.count(conn, "job_classifications")
    cost = cost_per_thousand(conn)
    dedup = dedup_score(DEDUP_SHEET) if DEDUP_SHEET.exists() else None
    pct = lambda a, b: f"{a / b:.1%}" if b else "–"  # noqa: E731

    lines = [
        "# Results",
        "",
        f"*Generated by `scripts/evaluate.py` on {now_iso()[:10]}. Aggregate numbers only.*",
        "",
        "## Collection and cleaning",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Postings collected (last 60 days) | {postings:,} |",
        *[f"| &nbsp;&nbsp;from {s} | {n:,} |" for s, n in per_source],
        f"| Unique vacancies after dedup | {vacancies:,} |",
        f"| Duplicates merged | {postings - vacancies:,} ({pct(postings - vacancies, postings)}) |",
        f"| Salary parsed | {pct(filled['salary_min'], postings)} |",
        f"| Education level parsed | {pct(filled['education_level'], postings)} |",
        f"| Experience parsed | {pct(filled['experience_years'], postings)} |",
        f"| Location mapped to a BPS regency | {pct(regency, postings)} |",
    ]
    if dedup and dedup["labelled_pairs"]:
        lines.append(f"| Dedup precision (hand-checked pairs, n={dedup['labelled_pairs']}) | {dedup['precision']} |")
    lines += ["", "## KBJI 2026 coding", ""]
    if metrics and metrics["labelled"]:
        def p(value):
            return f"{value:.1%}" if value is not None else "–"

        lines += [
            f"Hand-labelled sample: **{metrics['labelled']} vacancies**, stratified by source and predicted "
            "major group.",
            "",
            "| Level | Accuracy |",
            "|---|---|",
            f"| Major group (1 digit) | {p(metrics['accuracy_1'])} |",
            f"| Sub-major group (2 digits) | {p(metrics['accuracy_2'])} |",
            f"| Unit group (4 digits) | {p(metrics['accuracy_4'])} |",
            f"| Unit group, top-2 | {p(metrics['top2_accuracy_4'])} |",
            f"| Jabatan (6 digits, n={metrics['labelled_6_digit']}) | {p(metrics['accuracy_6'])} |",
            f"| 4-digit, confidence above threshold (n={metrics['n_confident']}) | "
            f"{p(metrics['accuracy_4_confident'])} |",
            f"| 4-digit, confidence below threshold (n={metrics['n_low']}) | "
            f"{p(metrics['accuracy_4_low_confidence'])} |",
        ]
    else:
        lines.append("*Not evaluated yet: label a sample with `scripts/evaluate.py --sample 200`, then `--score`.*")
    lines += [
        "",
        f"- Classifications flagged for review: {review:,} of {classified:,} ({pct(review, classified)})",
        f"- LLM tokens: {cost['input_tokens']:,} in / {cost['output_tokens']:,} out for "
        f"{cost['vacancies_coded']:,} vacancies coded",
        f"- Cost per 1,000 vacancies: {('US$' + str(cost['usd_per_1000'])) if cost['usd_per_1000'] else '–'} "
        f"({', '.join(cost.get('models') or ['no model yet'])}; list price from config; KBJI steps plus KBLI)",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=int, metavar="N", help="write a labelling sheet of N vacancies")
    parser.add_argument("--score", type=Path, metavar="CSV", help="score a filled labelling sheet")
    args = parser.parse_args(argv)
    conn = db.connect()
    if args.sample:
        print(f"wrote {write_sheet(conn, args.sample)} rows to {SHEET}")
        return 0
    metrics = score_sheet(args.score, config.llm()["review_threshold"]) if args.score else None
    if metrics:
        print(json.dumps(metrics, indent=1))
    write_results(conn, metrics)
    print(f"wrote {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
