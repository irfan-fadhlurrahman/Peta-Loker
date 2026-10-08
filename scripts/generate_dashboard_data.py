"""Write the dashboard's JSON files (summary, by_province, by_kbji, trend,
skills, salary, vacancies, ops) to dashboard/data/.

--public (the default, and the only mode `make deploy` accepts) applies the
publishing rules on top of the masking already done at ingestion:
  * no company names and no source URLs anywhere; a company appears only as
    "#" + the first 6 hex chars of its HMAC pseudonym, and its own name is
    blanked out of titles and descriptions ("Sales Trainee by [perusahaan]");
  * vacancy ids are hashed, so a row can't be traced back to a source posting;
  * descriptions are cut to masking.public_description_chars;
  * at most public.max_vacancies vacancies (the newest active ones);
  * every file carries "public": true and the time it was generated.
--local keeps names and URLs for looking at your own data; its files are
marked "public": false and the deploy check refuses them.

Usage:
    uv run python scripts/generate_dashboard_data.py --public --out-dir dashboard/data
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from core import config, db
from core.normalise import EDUCATION_LEVELS
from core.text import normalise_company, truncate
from core.timeutil import now_iso, today_jakarta

REPO_ROOT = Path(__file__).resolve().parent.parent
QUALITY_REPORT = REPO_ROOT / "data" / "quality" / "last.json"
COMPANY_MASK = "[perusahaan]"


def region_label(name: str) -> str:
    """'KOTA ADM. JAKARTA SELATAN' -> 'Kota Adm. Jakarta Selatan', keeping DKI/DI."""
    words = []
    for word in name.split():
        upper = word.upper()
        words.append(upper if upper in ("DKI", "DI") else word.capitalize())
    return " ".join(words)


def short_id(value: str) -> str:
    # The "v" prefix keeps an all-digit hash from ever looking like a phone number.
    return "v" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def hide_company(text: str | None, company: str | None) -> str | None:
    """Blank the company's own name out of a title/description."""
    if not text or not company:
        return text
    candidates = {company.strip(), normalise_company(company)}
    for name in sorted((c for c in candidates if c and len(c) >= 3), key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", COMPANY_MASK, text, flags=re.IGNORECASE)
    return text


def _best_classifications(conn) -> dict[str, dict]:
    """The latest usable classification per vacancy (ok preferred over partial)."""
    rows = conn.execute(
        """
        SELECT c.*, k6.title AS jabatan_title, k4.title AS unit_title, k1.code AS major_code, k1.title AS major_title
        FROM job_classifications c
        LEFT JOIN ref_kbji k6 ON k6.code = c.kbji_code
        LEFT JOIN ref_kbji k4 ON k4.code = c.kbji4
        LEFT JOIN ref_kbji k1 ON k1.code = substr(c.kbji4, 1, 1)
        WHERE c.status IN ('ok', 'partial')
        ORDER BY c.vacancy_id, c.status = 'ok', c.date_created
        """
    ).fetchall()
    return {r["vacancy_id"]: dict(r) for r in rows}  # later rows (ok, newest) win


def build(conn, public: bool = True, today: date | None = None) -> dict[str, dict]:
    today = today or today_jakarta()
    generated = {"public": public, "generated_at": now_iso()}
    max_vacancies = config.public()["max_vacancies"]
    desc_chars = config.masking()["public_description_chars"]

    regions = {r["region_code"]: dict(r) for r in conn.execute("SELECT * FROM ref_regions")}
    with (REPO_ROOT / "reference" / "kbli_sections.csv").open(encoding="utf-8") as f:
        kbli = {row["section"]: row["title"] for row in csv.DictReader(f)}
    majors = {r["code"]: r["title"] for r in conn.execute("SELECT code, title FROM ref_kbji WHERE level = 1")}
    classes = _best_classifications(conn)

    vacancies = conn.execute(
        """
        SELECT v.*, p.title, p.company_name, p.company_hmac, p.url, p.region_code, p.province_code, p.is_remote,
               p.salary_min, p.salary_max, p.education_level, p.experience_years, p.employment_type,
               p.description_masked, p.source_id, c.kbli_section
        FROM job_vacancies v
        JOIN job_postings p ON p.posting_id = v.canonical_posting_id
        LEFT JOIN job_companies c ON c.company_hmac = p.company_hmac
        ORDER BY v.first_seen DESC, v.vacancy_id
        """
    ).fetchall()
    active = [v for v in vacancies if v["is_active"]]
    skills_by_vacancy = defaultdict(list)
    for r in conn.execute("SELECT vacancy_id, skill_norm, skill_raw FROM job_skills"):
        skills_by_vacancy[r["vacancy_id"]].append(r["skill_raw"])

    postings_total = db.count(conn, "job_postings")
    week_ago = (today - timedelta(days=7)).isoformat()
    source_ids = {v["source_id"] for v in vacancies}

    # ------------------------------------------------------------ summary
    latest_runs = {}
    for r in db.latest_runs(conn, 1):
        latest_runs[r["source_id"]] = r
    enabled = [s for s in config.enabled_sources() if s in latest_runs] or sorted(source_ids)
    sources_ok = sum(1 for s in enabled if s in latest_runs and latest_runs[s]["status"] in ("success", "partial"))
    coded = sum(1 for v in active if v["vacancy_id"] in classes)
    summary = {
        **generated,
        "data_label": "demo" if source_ids <= {"sample"} else "real",
        "active_vacancies": len(active),
        "new_last_7_days": sum(1 for v in active if v["first_seen"] >= week_ago),
        "postings": postings_total,
        "duplicate_ratio": round((postings_total - len(vacancies)) / postings_total, 4) if postings_total else 0,
        "coded_ratio": round(coded / len(active), 4) if active else 0,
        "sources_ok": sources_ok,
        "sources_total": len(enabled),
        "window_days": config.collection()["max_age_days"],
        "first_seen_min": min((v["first_seen"] for v in vacancies), default=None),
        "last_seen_max": max((v["last_seen"] for v in vacancies), default=None),
    }

    # -------------------------------------------------------- by province
    per_province = Counter(v["province_code"] for v in active if v["province_code"])
    by_province = {**generated, "provinces": [
        {"code": code, "name": region_label(regions[code]["name"]), "n": per_province.get(code, 0)}
        for code in sorted(c for c, r in regions.items() if r["level"] == "province")
    ], "remote": sum(1 for v in active if v["is_remote"]),
        "unmapped": sum(1 for v in active if not v["province_code"] and not v["is_remote"])}

    # ------------------------------------------------------------ by KBJI
    major_counts = Counter(classes[v["vacancy_id"]]["major_code"] for v in active if v["vacancy_id"] in classes)
    unit_counts = Counter((classes[v["vacancy_id"]]["kbji4"], classes[v["vacancy_id"]]["unit_title"])
                          for v in active if v["vacancy_id"] in classes)
    by_kbji = {**generated, "coded": coded, "majors": [
        {"code": code, "title": majors[code], "n": major_counts.get(code, 0)} for code in sorted(majors)
    ], "top_units": [{"code": c, "title": t, "n": n} for (c, t), n in unit_counts.most_common(15)]}

    # -------------------------------------------------------------- trend
    first_seen = Counter(v["first_seen"] for v in vacancies)
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    trend = {**generated, "days": [{"date": d.isoformat(), "new": first_seen.get(d.isoformat(), 0)} for d in days]}

    # ------------------------------------------------------------- skills
    skill_counts, skill_display = Counter(), {}
    for v in active:
        for raw in skills_by_vacancy.get(v["vacancy_id"], []):
            key = raw.strip().lower()
            skill_counts[key] += 1
            skill_display.setdefault(key, raw.strip())
    skills = {**generated, "vacancies_with_skills": sum(1 for v in active if skills_by_vacancy.get(v["vacancy_id"])),
              "top": [{"skill": skill_display[k], "n": n} for k, n in skill_counts.most_common(20)]}

    # ------------------------------------------------------------- salary
    by_education = defaultdict(list)
    for v in active:
        if v["salary_min"] and v["education_level"]:
            by_education[v["education_level"]].append((v["salary_min"] + (v["salary_max"] or v["salary_min"])) / 2)
    salary = {**generated, "unit": "IDR per month (midpoint of the advertised range)", "levels": []}
    for level in EDUCATION_LEVELS:
        values = sorted(by_education.get(level, []))
        if len(values) >= 5:
            q1, q2, q3 = statistics.quantiles(values, n=4)
            salary["levels"].append({"education": level, "n": len(values), "p25": round(q1, -4),
                                     "median": round(q2, -4), "p75": round(q3, -4)})
    salary["with_salary"] = sum(len(v) for v in by_education.values())

    # ---------------------------------------------------------- vacancies
    records = []
    for v in active[:max_vacancies]:
        c = classes.get(v["vacancy_id"])
        region = regions.get(v["region_code"]) if v["region_code"] else None
        kbji_record = None
        if c:
            confidence = c["confidence"] if c["confidence"] is not None else c["confidence4"]
            kbji_record = {"code": c["kbji_code"], "title": c["jabatan_title"], "unit": c["kbji4"],
                           "unit_title": c["unit_title"], "confidence": confidence, "reason": c["reason"],
                           "needs_review": bool(c["needs_review"])}
        record = {
            "id": short_id(v["vacancy_id"]) if public else v["vacancy_id"],
            "title": hide_company(v["title"], v["company_name"]) if public else v["title"],
            "company": f"#{v['company_hmac'][:6]}" if v["company_hmac"] else None,
            "kbli": {"section": v["kbli_section"], "title": kbli.get(v["kbli_section"])} if v["kbli_section"] else None,
            "kbji": kbji_record,
            "region": {"code": v["region_code"], "name": region_label(region["name"]),
                       "province": region_label(regions[v["province_code"]]["name"])} if region else None,
            "remote": bool(v["is_remote"]),
            "salary": {"min": v["salary_min"], "max": v["salary_max"]} if v["salary_min"] else None,
            "education": v["education_level"],
            "experience_years": v["experience_years"],
            "employment_type": v["employment_type"],
            "n_sources": v["n_sources"],
            "first_seen": v["first_seen"],
            "last_seen": v["last_seen"],
            "skills": skills_by_vacancy.get(v["vacancy_id"], [])[:8],
            "description": truncate(hide_company(v["description_masked"], v["company_name"]) if public
                                    else v["description_masked"], desc_chars),
        }
        if not public:
            record.update({"company_name": v["company_name"], "url": v["url"], "source": v["source_id"]})
        records.append(record)
    vacancies_out = {**generated, "total_active": len(active), "shown": len(records), "vacancies": records}

    # ---------------------------------------------------------------- ops
    runs = defaultdict(list)
    for r in db.latest_runs(conn, 14):
        runs[r["source_id"]].append({"started_at": r["started_at"], "status": r["status"], "n_saved": r["n_saved"],
                                     "n_fetched": r["n_fetched"], "n_errors": r["n_errors"],
                                     "error_type": r["error_type"]})
    sources_cfg = config.sources()
    usage = conn.execute(
        """
        SELECT step, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
               SUM(n_items) AS items
        FROM llm_usage GROUP BY step ORDER BY step
        """
    ).fetchall()
    confidences = [c["confidence"] if c["confidence"] is not None else c["confidence4"] for c in classes.values()]
    histogram = [0] * 10
    for value in confidences:
        if value is not None:
            histogram[min(int(value * 10), 9)] += 1
    quality = json.loads(QUALITY_REPORT.read_text(encoding="utf-8")) if QUALITY_REPORT.exists() else None
    ops = {
        **generated,
        "sources": [{"source": s, "class": sources_cfg.get(s, {}).get("class", "").rsplit(".", 1)[-1],
                     "runs": runs.get(s, [])} for s in sorted(runs)],
        "quality": quality,
        "llm": {"steps": [dict(u) for u in usage],
                "review_threshold": config.llm()["review_threshold"],
                "needs_review": sum(1 for c in classes.values() if c["needs_review"]),
                "confidence_histogram": histogram},
    }
    return {"summary": summary, "by_province": by_province, "by_kbji": by_kbji, "trend": trend,
            "skills": skills, "salary": salary, "vacancies": vacancies_out, "ops": ops}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--public", action="store_true", default=True, help="publishable output (default)")
    mode.add_argument("--local", action="store_true", help="keep company names and URLs (never deploy)")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "dashboard" / "data")
    args = parser.parse_args(argv)
    files = build(db.connect(), public=not args.local)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (args.out_dir / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                                    encoding="utf-8")
    s = files["summary"]
    print(f"wrote {len(files)} files to {args.out_dir} (public={s['public']}, {s['active_vacancies']} active "
          f"vacancies, {files['vacancies']['shown']} shown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
