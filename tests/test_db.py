from __future__ import annotations

import sqlite3

import pytest

from core import db


def _posting(**overrides):
    row = {
        "posting_id": "dealls:abc",
        "source_id": "dealls",
        "source_job_id": "abc",
        "url": "https://dealls.com/loker/abc",
        "title": "Data Analyst",
        "fetched_at": "2026-10-08T10:00:00+07:00",
    }
    row.update(overrides)
    return row


def test_schema_creates_every_table(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables >= {
        "job_sources", "job_run_logs", "llm_usage", "ref_kbji", "ref_regions", "job_vacancies",
        "job_companies", "job_postings", "job_classifications", "job_skills",
    }


def test_init_schema_is_idempotent(conn):
    db.init_schema(conn)
    db.init_schema(conn)


def test_upsert_posting_is_idempotent_and_keeps_first_fetched(conn):
    db.upsert_source(conn, "dealls", "sources.dealls.DeallsSource", "https://dealls.com")
    db.upsert_postings(conn, [_posting()])
    db.upsert_postings(conn, [_posting(title="Senior Data Analyst", fetched_at="2026-10-09T10:00:00+07:00")])
    rows = conn.execute("SELECT title, first_fetched_at, fetched_at FROM job_postings").fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "Senior Data Analyst"
    assert rows[0]["first_fetched_at"] == "2026-10-08T10:00:00+07:00"
    assert rows[0]["fetched_at"] == "2026-10-09T10:00:00+07:00"


def test_upsert_posting_rejects_missing_required_field(conn):
    db.upsert_source(conn, "dealls", "x", None)
    with pytest.raises(ValueError, match="title"):
        db.upsert_postings(conn, [_posting(title="")])


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert_postings(conn, [_posting(source_id="unknown")])


def test_run_log_round_trip(conn):
    db.upsert_source(conn, "dealls", "x", None)
    db.insert_run(conn, "r1", "dealls", "2026-10-08T10:00:00+07:00")
    db.finish_run(conn, "r1", "partial", {"n_listed": 5, "n_saved": 3, "n_errors": 2}, "layout_changed", "{}")
    row = conn.execute("SELECT * FROM job_run_logs WHERE run_id = 'r1'").fetchone()
    assert (row["status"], row["n_listed"], row["n_saved"], row["n_errors"]) == ("partial", 5, 3, 2)
    assert row["finished_at"]
    assert db.latest_runs(conn)[0]["run_id"] == "r1"


def test_reference_data_loads_with_parents_before_children(conn_with_reference):
    conn = conn_with_reference
    assert db.count(conn, "ref_kbji", "level = 5") == 2380
    assert db.count(conn, "ref_kbji", "level = 4") == 447
    assert db.count(conn, "ref_regions", "level = 'province'") == 38
    assert db.count(conn, "ref_regions", "level = 'regency'") == 514
    row = conn.execute("SELECT title, parent_code FROM ref_kbji WHERE code = '2512.03'").fetchone()
    assert row["parent_code"] == "2512"
    assert "Perangkat Lunak" in row["title"]
