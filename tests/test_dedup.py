from __future__ import annotations

from datetime import date

from core import db
from core.base import content_hash
from core.dedup import PostingKey, cluster, is_match
from scripts.dedup import run as run_dedup

SETTINGS = {"window_days": 30, "title_min_ratio": 90, "description_min_ratio": 85, "description_chars": 500,
            "inactive_after_days": 14}
DESC = "mengolah data penjualan harian membuat dasbor untuk tim penjualan dan menyusun laporan bulanan"


def _key(pid, title, company="abc", province="32", region="3273", desc=DESC, day=date(2026, 10, 1), h=None):
    return PostingKey(pid, pid.split(":")[0], title, company, province, region, desc, h, day)


def test_cross_source_near_identical_postings_match():
    a = _key("glints:1", "Data Analyst")
    b = _key("dealls:2", "Data Analyst (Bandung)", desc=DESC + " segera")
    assert is_match(a, b, SETTINGS)


def test_same_company_different_role_does_not_match():
    a = _key("glints:1", "Sales Executive")
    b = _key("glints:2", "Sales Manager")
    assert not is_match(a, b, SETTINGS)


def test_same_title_different_description_does_not_match_without_shared_regency():
    a = _key("glints:1", "Customer Service", region="32")
    b = _key("glints:2", "Customer Service", region="32", desc="melayani pelanggan di toko ritel cabang baru")
    assert not is_match(a, b, SETTINGS)


def test_same_title_same_regency_matches():
    a = _key("glints:1", "Customer Service")
    b = _key("kalibrr:2", "Customer Service", desc="teks berbeda sama sekali")
    assert is_match(a, b, SETTINGS)


def test_cluster_uses_hash_and_respects_window_and_blocks():
    keys = [
        _key("a:1", "Data Analyst", h="H1"),
        _key("a:2", "Data Analyst", h="H1", company="other"),        # same hash: merged even across blocks
        _key("b:3", "Data Analyst", day=date(2026, 12, 15)),           # > 30 days later: separate
        _key("c:4", "Data Analyst", province="31", region="3171"),     # other province: separate
    ]
    groups = sorted(sorted(g) for g in cluster(keys, SETTINGS).groups().values())
    assert groups == [["a:1", "a:2"], ["b:3"], ["c:4"]]


def _insert(conn, pid, title, company, desc, posted, fetched="2026-10-08T09:00:00+07:00", valid=None):
    source, job_id = pid.split(":")
    db.upsert_source(conn, source, "x", None)
    db.upsert_postings(conn, [{
        "posting_id": pid, "source_id": source, "source_job_id": job_id, "url": f"https://x/{pid}", "title": title,
        "company_name": company, "description_masked": desc, "posted_at": posted, "fetched_at": fetched,
        "valid_through": valid, "content_hash": content_hash(title, company, "Kota Bandung", desc),
    }])
    conn.execute("UPDATE job_postings SET province_code = '32', region_code = '3273' WHERE posting_id = ?", (pid,))
    conn.commit()


def test_run_builds_vacancies_with_stable_ids_and_activity(conn_with_reference):
    conn = conn_with_reference
    _insert(conn, "glints:1", "Data Analyst", "PT Contoh", DESC, "2026-10-01")
    _insert(conn, "dealls:2", "Data Analyst", "PT. Contoh Tbk", DESC + " segera", "2026-10-03")
    _insert(conn, "lokerid:3", "Barista", "PT Kopi", "membuat kopi", "2026-09-01",
            fetched="2026-09-01T09:00:00+07:00")
    stats = run_dedup(conn, SETTINGS, today=date(2026, 10, 8))
    assert stats == {"postings": 3, "vacancies": 2, "merged": 1, "cross_source": 1, "active": 1}
    vac = conn.execute("SELECT * FROM job_vacancies WHERE n_postings = 2").fetchone()
    assert vac["vacancy_id"] == "glints:1" and vac["first_seen"] == "2026-10-01"

    # A later posting joins the cluster: the vacancy keeps its id.
    _insert(conn, "kalibrr:9", "Data Analyst", "Contoh", DESC, "2026-09-28")
    run_dedup(conn, SETTINGS, today=date(2026, 10, 8))
    ids = {r[0] for r in conn.execute("SELECT DISTINCT vacancy_id FROM job_postings WHERE title = 'Data Analyst'")}
    assert ids == {"glints:1"}
    assert db.count(conn, "job_vacancies") == 2
