"""Every SQL statement in the project lives in this module.

The store is SQLite (stdlib sqlite3) but the SQL is deliberately portable —
plain ANSI plus `INSERT ... ON CONFLICT ... DO UPDATE`, TEXT keys built in
Python, ISO-8601 TEXT timestamps — so moving to PostgreSQL later means
rewriting connect() and little else. See the header of db/schema.sql.

DB_PATH (env, optional) overrides the default data/peta_loker.db; tests pass
":memory:".
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import core.env  # noqa: F401  (import for side effect: loads .env)
from core.timeutil import now_iso

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "db" / "schema.sql"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "peta_loker.db"

# Column order for job_postings upserts. Everything except the key and
# first_fetched_at is refreshed on conflict, so a re-scrape updates a listing
# in place instead of creating a second row.
POSTING_COLUMNS = (
    "posting_id", "source_id", "source_job_id", "url", "raw_key", "title", "company_name",
    "company_hmac", "location_raw", "region_code", "province_code", "is_remote",
    "salary_raw", "salary_min", "salary_max", "salary_period",
    "education_raw", "education_level", "experience_raw", "experience_years",
    "employment_type_raw", "employment_type", "description_masked", "extra_json",
    "content_hash", "posted_at", "valid_through", "first_fetched_at", "fetched_at",
)
_POSTING_KEEP_ON_CONFLICT = {"posting_id", "first_fetched_at"}
_POSTING_DEFAULTS = {"is_remote": 0}

RUN_COUNT_COLUMNS = ("n_listed", "n_fetched", "n_parsed", "n_saved", "n_skipped", "n_errors")


def db_path() -> str:
    return os.environ.get("DB_PATH") or str(DEFAULT_DB_PATH)


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open the database with foreign keys on and rows addressable by name.
    WAL lets the dashboard generator read while a scraper writes."""
    path = path or db_path()
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create every table and index that doesn't exist yet. Safe to re-run."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()) -> int:
    """Row count for a table (table name is never user input)."""
    sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
    return conn.execute(sql, params).fetchone()[0]


# ------------------------------------------------------------------ sources


def upsert_source(conn: sqlite3.Connection, source_id: str, class_name: str, base_url: str | None,
                  is_active: bool = True) -> None:
    now = now_iso()
    conn.execute(
        """
        INSERT INTO job_sources (source_id, class_name, base_url, is_active, date_created, date_modified)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (source_id) DO UPDATE SET
            class_name = excluded.class_name,
            base_url = excluded.base_url,
            is_active = excluded.is_active,
            date_modified = excluded.date_modified
        """,
        (source_id, class_name, base_url, int(is_active), now, now),
    )
    conn.commit()


# ------------------------------------------------------------------ run logs


def insert_run(conn: sqlite3.Connection, run_id: str, source_id: str, started_at: str) -> None:
    conn.execute(
        "INSERT INTO job_run_logs (run_id, source_id, started_at, status) VALUES (?, ?, ?, 'running')",
        (run_id, source_id, started_at),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, counts: dict[str, int],
               error_type: str | None = None, message: str | None = None) -> None:
    sets = ", ".join(f"{c} = ?" for c in RUN_COUNT_COLUMNS)
    conn.execute(
        f"""
        UPDATE job_run_logs
        SET finished_at = ?, status = ?, error_type = ?, message = ?, {sets}
        WHERE run_id = ?
        """,
        (now_iso(), status, error_type, message, *(int(counts.get(c, 0)) for c in RUN_COUNT_COLUMNS), run_id),
    )
    conn.commit()


def latest_runs(conn: sqlite3.Connection, limit_per_source: int = 14) -> list[sqlite3.Row]:
    """Most recent runs per source, newest first (for ops views)."""
    return conn.execute(
        """
        SELECT * FROM (
            SELECT r.*, ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY started_at DESC) AS rn
            FROM job_run_logs r
        ) ranked
        WHERE rn <= ?
        ORDER BY source_id, started_at DESC
        """,
        (limit_per_source,),
    ).fetchall()


# ------------------------------------------------------------------ postings


def posting_fetched_at(conn: sqlite3.Connection, posting_id: str) -> str | None:
    row = conn.execute("SELECT fetched_at FROM job_postings WHERE posting_id = ?", (posting_id,)).fetchone()
    return row["fetched_at"] if row else None


def touch_posting(conn: sqlite3.Connection, posting_id: str, seen_at: str) -> None:
    """Mark a posting as seen again (it reappeared in a listing) without
    re-downloading it. fetched_at doubles as "last seen" for the lifecycle."""
    conn.execute("UPDATE job_postings SET fetched_at = ? WHERE posting_id = ?", (seen_at, posting_id))
    conn.commit()


def upsert_postings(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    """Insert or refresh postings. Each row is a dict keyed by POSTING_COLUMNS
    (missing keys become NULL). Returns the number of rows written."""
    rows = list(rows)
    if not rows:
        return 0
    for row in rows:
        for required in ("posting_id", "source_id", "source_job_id", "url", "title", "fetched_at"):
            if not row.get(required):
                raise ValueError(f"posting missing {required!r}: {row.get('posting_id')!r}")
    cols = ", ".join(POSTING_COLUMNS)
    marks = ", ".join("?" for _ in POSTING_COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in POSTING_COLUMNS if c not in _POSTING_KEEP_ON_CONFLICT)
    conn.executemany(
        f"INSERT INTO job_postings ({cols}) VALUES ({marks}) "
        f"ON CONFLICT (posting_id) DO UPDATE SET {updates}",
        [
            tuple(
                (row.get("first_fetched_at") or row["fetched_at"]) if c == "first_fetched_at"
                else (row.get(c) if row.get(c) is not None else _POSTING_DEFAULTS.get(c))
                for c in POSTING_COLUMNS
            )
            for row in rows
        ],
    )
    conn.commit()
    return len(rows)


def upsert_company(conn: sqlite3.Connection, company_hmac: str, name_local: str) -> None:
    conn.execute(
        """
        INSERT INTO job_companies (company_hmac, name_local, date_created) VALUES (?, ?, ?)
        ON CONFLICT (company_hmac) DO NOTHING
        """,
        (company_hmac, name_local, now_iso()),
    )


# ----------------------------------------------------------------- reference


def upsert_ref_kbji(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    """rows: {code, level, title, parent_code}. Parents must come before
    children (the builder emits them in that order) for the FK to hold."""
    rows = list(rows)
    conn.executemany(
        """
        INSERT INTO ref_kbji (code, level, title, parent_code) VALUES (:code, :level, :title, :parent_code)
        ON CONFLICT (code) DO UPDATE SET
            level = excluded.level, title = excluded.title, parent_code = excluded.parent_code
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_ref_regions(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    """rows: {region_code, name, level, kind, province_code}."""
    rows = list(rows)
    conn.executemany(
        """
        INSERT INTO ref_regions (region_code, name, level, kind, province_code)
        VALUES (:region_code, :name, :level, :kind, :province_code)
        ON CONFLICT (region_code) DO UPDATE SET
            name = excluded.name, level = excluded.level, kind = excluded.kind,
            province_code = excluded.province_code
        """,
        rows,
    )
    conn.commit()
    return len(rows)


# ----------------------------------------------------------------- normalise


def postings_to_normalise(conn: sqlite3.Connection, only_new: bool = False) -> list[sqlite3.Row]:
    where = "WHERE normalised_at IS NULL OR normalised_at < fetched_at" if only_new else ""
    return conn.execute(
        f"""
        SELECT posting_id, location_raw, salary_raw, education_raw, experience_raw, employment_type_raw, extra_json
        FROM job_postings {where}
        """
    ).fetchall()


def update_normalised(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    rows = list(rows)
    conn.executemany(
        """
        UPDATE job_postings SET
            salary_min = :salary_min, salary_max = :salary_max, salary_period = :salary_period,
            education_level = :education_level, experience_years = :experience_years,
            employment_type = :employment_type, region_code = :region_code, province_code = :province_code,
            is_remote = :is_remote, normalised_at = :normalised_at
        WHERE posting_id = :posting_id
        """,
        rows,
    )
    conn.commit()
    return len(rows)


# --------------------------------------------------------------------- dedup


def postings_for_dedup(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT posting_id, source_id, title, company_name, province_code, region_code, description_masked,
               content_hash, posted_at, first_fetched_at, fetched_at, valid_through, vacancy_id
        FROM job_postings
        """
    ).fetchall()


def save_vacancies(conn: sqlite3.Connection, vacancies: list[dict], assignments: dict[str, str]) -> None:
    """Write the clustering result in one transaction: upsert every vacancy,
    point each posting at its vacancy, and drop vacancies that no posting
    references any more (merged into another), with their LLM rows."""
    try:
        conn.executemany(
            """
            INSERT INTO job_vacancies (vacancy_id, canonical_posting_id, first_seen, last_seen, n_postings,
                                       n_sources, is_active, date_modified)
            VALUES (:vacancy_id, :canonical_posting_id, :first_seen, :last_seen, :n_postings, :n_sources,
                    :is_active, :date_modified)
            ON CONFLICT (vacancy_id) DO UPDATE SET
                canonical_posting_id = excluded.canonical_posting_id, first_seen = excluded.first_seen,
                last_seen = excluded.last_seen, n_postings = excluded.n_postings, n_sources = excluded.n_sources,
                is_active = excluded.is_active, date_modified = excluded.date_modified
            """,
            vacancies,
        )
        conn.executemany("UPDATE job_postings SET vacancy_id = ? WHERE posting_id = ?",
                         [(vid, pid) for pid, vid in assignments.items()])
        orphan = "SELECT vacancy_id FROM job_vacancies WHERE vacancy_id NOT IN (SELECT DISTINCT vacancy_id " \
                 "FROM job_postings WHERE vacancy_id IS NOT NULL)"
        conn.execute(f"DELETE FROM job_classifications WHERE vacancy_id IN ({orphan})")
        conn.execute(f"DELETE FROM job_skills WHERE vacancy_id IN ({orphan})")
        conn.execute(f"DELETE FROM job_vacancies WHERE vacancy_id IN ({orphan})")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# -------------------------------------------------------------------- enrich


def vacancies_to_classify(conn: sqlite3.Connection, model: str, prompt_version: str, limit: int | None = None,
                          vacancy_ids: list[str] | None = None) -> list[sqlite3.Row]:
    """Canonical posting of every vacancy without a successful classification
    for this model and prompt version, newest first."""
    params: list = [model, prompt_version]
    where = ""
    if vacancy_ids is not None:
        where = f"AND v.vacancy_id IN ({', '.join('?' for _ in vacancy_ids)})"
        params.extend(vacancy_ids)
    sql = f"""
        SELECT v.vacancy_id, p.title, p.extra_json, p.education_raw, p.description_masked
        FROM job_vacancies v
        JOIN job_postings p ON p.posting_id = v.canonical_posting_id
        WHERE NOT EXISTS (
            SELECT 1 FROM job_classifications c
            WHERE c.vacancy_id = v.vacancy_id AND c.model = ? AND c.prompt_version = ? AND c.status = 'ok'
        ) {where}
        ORDER BY v.first_seen DESC, v.vacancy_id
    """
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def save_classifications(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO job_classifications (vacancy_id, model, prompt_version, kbji4, alt4, confidence4, kbji_code,
                                         confidence, reason, needs_review, pii_found, status, date_created)
        VALUES (:vacancy_id, :model, :prompt_version, :kbji4, :alt4, :confidence4, :kbji_code, :confidence,
                :reason, :needs_review, :pii_found, :status, :date_created)
        ON CONFLICT (vacancy_id, model, prompt_version) DO UPDATE SET
            kbji4 = excluded.kbji4, alt4 = excluded.alt4, confidence4 = excluded.confidence4,
            kbji_code = excluded.kbji_code, confidence = excluded.confidence, reason = excluded.reason,
            needs_review = excluded.needs_review, pii_found = excluded.pii_found, status = excluded.status,
            date_created = excluded.date_created
        """,
        rows,
    )
    conn.commit()


def save_skills(conn: sqlite3.Connection, vacancy_id: str, skills: list[tuple[str, str]], model: str,
                prompt_version: str) -> None:
    """Replace a vacancy's skills. skills: [(skill_norm, skill_raw)]."""
    conn.execute("DELETE FROM job_skills WHERE vacancy_id = ?", (vacancy_id,))
    conn.executemany(
        """
        INSERT INTO job_skills (vacancy_id, skill_norm, skill_raw, model, prompt_version) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (vacancy_id, skill_norm) DO NOTHING
        """,
        [(vacancy_id, norm, raw, model, prompt_version) for norm, raw in skills],
    )
    conn.commit()


def log_llm_call(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO llm_usage (call_id, step, model, prompt_version, n_items, input_tokens, output_tokens,
                               latency_ms, status, date_created)
        VALUES (:call_id, :step, :model, :prompt_version, :n_items, :input_tokens, :output_tokens, :latency_ms,
                :status, :date_created)
        """,
        row,
    )
    conn.commit()


def companies_to_code(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """Companies without a KBLI section, with hints: the industry a source
    states for them and a few titles they advertise."""
    sql = """
        SELECT c.company_hmac, c.name_local FROM job_companies c
        WHERE c.kbli_section IS NULL
          AND EXISTS (SELECT 1 FROM job_postings p WHERE p.company_hmac = c.company_hmac)
        ORDER BY c.company_hmac
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    out = []
    for company in conn.execute(sql).fetchall():
        posts = conn.execute(
            "SELECT title, extra_json FROM job_postings WHERE company_hmac = ? ORDER BY fetched_at DESC LIMIT 5",
            (company["company_hmac"],),
        ).fetchall()
        industries = []
        for p in posts:
            extra = json.loads(p["extra_json"]) if p["extra_json"] else {}
            if extra.get("industry"):
                industries.append(extra["industry"])
        out.append({"company_hmac": company["company_hmac"], "name": company["name_local"],
                    "industry_hint": industries[0] if industries else None,
                    "titles": list(dict.fromkeys(p["title"] for p in posts))[:3]})
    return out


def save_company_kbli(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        UPDATE job_companies SET kbli_section = :kbli_section, kbli_model = :model,
               kbli_prompt_version = :prompt_version
        WHERE company_hmac = :company_hmac
        """,
        rows,
    )
    conn.commit()
