from __future__ import annotations

import csv

from scripts.evaluate import score_sheet
from scripts.export import flatten


def _sheet(tmp_path, rows):
    path = tmp_path / "labels.csv"
    fields = ["vacancy_id", "predicted_kbji_code", "predicted_kbji4", "alt4", "confidence", "gold_kbji_code"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_scores_prefix_accuracy_top2_and_confidence_split(tmp_path):
    path = _sheet(tmp_path, [
        {"vacancy_id": "a", "predicted_kbji_code": "2512.03", "predicted_kbji4": "2512", "alt4": "2511",
         "confidence": "0.9", "gold_kbji_code": "2512.03"},                      # exact
        {"vacancy_id": "b", "predicted_kbji_code": "2512.01", "predicted_kbji4": "2512", "alt4": "2511",
         "confidence": "0.8", "gold_kbji_code": "2511.01"},                      # right 2 digits, top-2 hit
        {"vacancy_id": "c", "predicted_kbji_code": "4110.00", "predicted_kbji4": "4110", "alt4": "",
         "confidence": "0.4", "gold_kbji_code": "5230"},                         # wrong; gold only 4-digit
        {"vacancy_id": "d", "predicted_kbji_code": "5132.01", "predicted_kbji4": "5132", "alt4": "",
         "confidence": "0.95", "gold_kbji_code": ""},                            # unlabelled: skipped
    ])
    m = score_sheet(path, threshold=0.7)
    assert m["labelled"] == 3
    assert (m["accuracy_1"], m["accuracy_2"], m["accuracy_4"]) == (0.667, 0.667, 0.333)
    assert m["top2_accuracy_4"] == 0.667
    assert (m["accuracy_6"], m["labelled_6_digit"]) == (0.5, 2)
    assert (m["accuracy_4_confident"], m["n_confident"]) == (0.5, 2)
    assert (m["accuracy_4_low_confidence"], m["n_low"]) == (0.0, 1)


def test_export_flatten_keeps_public_fields_only():
    record = {"id": "v1", "title": "Barista", "company": "#abc123", "kbji": None, "region": None, "salary": None,
              "kbli": None, "remote": False, "education": "SMA/SMK", "experience_years": 0,
              "employment_type": "full_time", "n_sources": 1, "first_seen": "2026-10-01", "last_seen": "2026-10-08",
              "skills": ["kopi"], "description": "Membuat kopi"}
    row = flatten(record)
    assert row["skills"] == "kopi" and "url" not in row and "company_name" not in row
    assert "url" in flatten({**record, "url": "https://x", "company_name": "PT X", "source": "glints"})
