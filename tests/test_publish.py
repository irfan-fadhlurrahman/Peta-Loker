"""Quality gate, public export rules, deploy guard and the demo — the steps
that decide what may be published."""

from __future__ import annotations

import json

import pytest

from core import db
from scripts import deploy_check, quality_check
from scripts.demo import main as demo_main
from scripts.generate_dashboard_data import build, hide_company, region_label, short_id


def _posting(conn, pid, title, description="Mengolah data", company="PT Contoh Jaya"):
    source, job_id = pid.split(":")
    db.upsert_source(conn, source, "x", None)
    db.upsert_company(conn, "abc123def456", company)
    db.upsert_postings(conn, [{"posting_id": pid, "source_id": source, "source_job_id": job_id,
                               "url": f"https://site/{job_id}", "title": title, "company_name": company,
                               "company_hmac": "abc123def456", "location_raw": "Kota Bandung",
                               "description_masked": description, "fetched_at": "2026-10-08T09:00:00+07:00"}])


def test_quality_gate_fails_on_leftover_contact_details(conn_with_reference):
    conn = conn_with_reference
    _posting(conn, "glints:1", "Analis Data")
    assert "no_contact_details_in_clean_tables" not in quality_check.run_checks(conn)["fail"]
    _posting(conn, "glints:2", "Analis", description="WA 0812-3456-7890")
    report = quality_check.run_checks(conn)
    assert report["status"] == "fail" and "no_contact_details_in_clean_tables" in report["fail"]


def test_quality_gate_fails_on_invalid_kbji_code(conn_with_reference):
    conn = conn_with_reference
    _posting(conn, "glints:1", "Analis Data")
    conn.execute("INSERT INTO job_vacancies VALUES ('glints:1', 'glints:1', '2026-10-01', '2026-10-08', 1, 1, 1, 'x')")
    conn.commit()  # the pragma is ignored inside an open transaction
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO job_classifications (vacancy_id, model, prompt_version, kbji4, status, date_created) "
                 "VALUES ('glints:1', 'm', 'v1', '9999', 'partial', 'x')")
    assert "kbji_codes_valid" in quality_check.run_checks(conn)["fail"]


def test_public_helpers():
    assert hide_company("Sales Trainee by PT Contoh Jaya", "PT Contoh Jaya") == "Sales Trainee by [perusahaan]"
    assert hide_company("Barista Contoh Jaya Bandung", "PT. Contoh Jaya Tbk") == "Barista [perusahaan] Bandung"
    assert region_label("KOTA ADM. JAKARTA SELATAN") == "Kota Adm. Jakarta Selatan"
    assert region_label("DKI JAKARTA") == "DKI Jakarta"
    assert short_id("glints:1").startswith("v") and len(short_id("glints:1")) == 11


def test_public_export_has_no_names_urls_or_traceable_ids(conn_with_reference):
    conn = conn_with_reference
    _posting(conn, "glints:1", "Barista PT Contoh Jaya")
    conn.execute("UPDATE job_postings SET region_code = '3273', province_code = '32'")
    conn.execute("INSERT INTO job_vacancies VALUES ('glints:1', 'glints:1', '2026-10-07', '2026-10-08', 1, 1, 1, 'x')")
    files = build(conn, public=True)
    text = json.dumps(files, ensure_ascii=False)
    assert "PT Contoh Jaya" not in text and "https://site/" not in text and "glints:1" not in text
    record = files["vacancies"]["vacancies"][0]
    assert record["company"] == "#abc123" and record["title"] == "Barista [perusahaan]"
    assert all(payload["public"] is True for payload in files.values())
    local = build(conn, public=False)
    assert local["vacancies"]["vacancies"][0]["company_name"] == "PT Contoh Jaya"
    assert local["summary"]["public"] is False


@pytest.fixture
def published(tmp_path):
    data_dir, report = tmp_path / "data", tmp_path / "last.json"
    data_dir.mkdir()
    report.write_text(json.dumps({"status": "pass", "fail": [], "checked_at": "2026-10-08T09:00:00+07:00"}))
    for name in deploy_check.REQUIRED_FILES:
        (data_dir / f"{name}.json").write_text(json.dumps({"public": True, "generated_at": "2026-10-08T10:00:00+07:00",
                                                           "items": [{"title": "Barista"}]}))
    return data_dir, report


def test_deploy_check_passes_clean_public_data(published):
    assert deploy_check.check(*published) == []


def test_deploy_check_blocks_local_files_pii_and_failed_quality(published):
    data_dir, report = published
    (data_dir / "vacancies.json").write_text(json.dumps(
        {"public": False, "generated_at": "2026-10-08T10:00:00+07:00",
         "vacancies": [{"title": "Kasir", "description": "hubungi kasir@example.com", "url": "https://x"}]}))
    report.write_text(json.dumps({"status": "fail", "fail": ["kbji_codes_valid"], "checked_at": "2026-10-08T09:00"}))
    problems = " | ".join(deploy_check.check(data_dir, report))
    assert "not marked public" in problems
    assert "email" in problems
    assert "forbidden field 'url'" in problems
    assert "did not pass" in problems


def test_deploy_check_blocks_stale_data(published):
    data_dir, report = published
    report.write_text(json.dumps({"status": "pass", "fail": [], "checked_at": "2026-10-09T00:00:00+07:00"}))
    assert any("ran after" in p for p in deploy_check.check(data_dir, report))


def test_demo_runs_offline_end_to_end(tmp_path, monkeypatch):
    import scripts.demo as demo
    from core import raw_store

    monkeypatch.setattr(demo, "DEMO_DB", tmp_path / "demo.db")
    monkeypatch.setattr(demo, "OUT_DIR", tmp_path / "dashboard")
    monkeypatch.setattr(quality_check, "REPORT_PATH", tmp_path / "quality" / "last.json")
    monkeypatch.setattr(raw_store, "LOCAL_ROOT", tmp_path / "raw")
    raw_store.get_raw_store.cache_clear()
    monkeypatch.setattr(raw_store, "get_raw_store", lambda: raw_store.LocalRawStore(tmp_path / "raw"))
    import core.base
    monkeypatch.setattr(core.base, "get_raw_store", lambda: raw_store.LocalRawStore(tmp_path / "raw"))
    assert demo_main([]) == 0
    summary = json.loads((tmp_path / "dashboard" / "summary.json").read_text(encoding="utf-8"))
    assert summary["data_label"] == "demo" and summary["active_vacancies"] > 400 and summary["coded_ratio"] > 0.9
