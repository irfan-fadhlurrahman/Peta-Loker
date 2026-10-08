-- Peta Loker schema (SQLite, written in portable SQL).
--
-- Portability rules (so a later move to PostgreSQL is a one-module change):
--   * no AUTOINCREMENT / SERIAL — every key is a deterministic TEXT id built
--     in Python (e.g. posting_id = '<source>:<source_job_id>'), which also
--     makes re-runs idempotent;
--   * booleans are INTEGER 0/1, timestamps are ISO-8601 TEXT with +07:00,
--     JSON is TEXT;
--   * upserts use INSERT ... ON CONFLICT ... DO UPDATE (SQLite >= 3.24 and
--     PostgreSQL both accept it); never INSERT OR REPLACE, which deletes rows.
--
-- Table groups: job_* core data, ref_* reference data (rebuilt from public
-- sources by scripts/build_*.py), and the operational tables job_sources,
-- job_run_logs and llm_usage.

-- ---------------------------------------------------------------- operations

-- Source register: one row per configured source.
CREATE TABLE IF NOT EXISTS job_sources (
    source_id      TEXT PRIMARY KEY,         -- config key, e.g. 'glints'
    class_name     TEXT NOT NULL,            -- dotted path of the JobSource subclass
    base_url       TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1,
    date_created   TEXT NOT NULL,
    date_modified  TEXT NOT NULL
);

-- One row per source run. Counts make a run auditable without reading logs.
CREATE TABLE IF NOT EXISTS job_run_logs (
    run_id       TEXT PRIMARY KEY,           -- uuid4 hex
    source_id    TEXT NOT NULL REFERENCES job_sources (source_id),
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,              -- running | success | partial | failed | blocked
    error_type   TEXT,                       -- robots_disallowed | blocked | layout_changed | timeout | parse_error | ...
    message      TEXT,
    n_listed     INTEGER NOT NULL DEFAULT 0, -- job URLs discovered
    n_fetched    INTEGER NOT NULL DEFAULT 0, -- pages downloaded
    n_parsed     INTEGER NOT NULL DEFAULT 0, -- postings parsed
    n_saved      INTEGER NOT NULL DEFAULT 0, -- postings upserted (inside the age window)
    n_skipped    INTEGER NOT NULL DEFAULT 0, -- too old / already fresh / disallowed
    n_errors     INTEGER NOT NULL DEFAULT 0  -- per-item failures (not fatal)
);
CREATE INDEX IF NOT EXISTS idx_run_logs_source_started ON job_run_logs (source_id, started_at);

-- Tokens and latency for every LLM call.
CREATE TABLE IF NOT EXISTS llm_usage (
    call_id         TEXT PRIMARY KEY,        -- uuid4 hex
    step            TEXT NOT NULL,           -- kbji4 | kbji6 | kbli
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    n_items         INTEGER NOT NULL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    latency_ms      INTEGER,
    status          TEXT NOT NULL,           -- ok | failed | filtered
    date_created    TEXT NOT NULL
);

-- ------------------------------------------------------------------ reference

-- KBJI 2026 (Peraturan BPS No. 7 Tahun 2026, Lampiran). level 1 = golongan
-- pokok (1 digit), 2 = subgolongan pokok, 3 = golongan, 4 = subgolongan
-- (4 digits, the ISCO unit group), 5 = jabatan (6 chars, 'NNNN.NN').
CREATE TABLE IF NOT EXISTS ref_kbji (
    code         TEXT PRIMARY KEY,
    level        INTEGER NOT NULL,
    title        TEXT NOT NULL,
    parent_code  TEXT REFERENCES ref_kbji (code)
);
CREATE INDEX IF NOT EXISTS idx_ref_kbji_parent ON ref_kbji (parent_code);

-- BPS province (2-digit) and regency/city (4-digit) codes.
CREATE TABLE IF NOT EXISTS ref_regions (
    region_code    TEXT PRIMARY KEY,
    name           TEXT NOT NULL,            -- as published by BPS, e.g. 'KOTA BANDUNG'
    level          TEXT NOT NULL,            -- province | regency
    kind           TEXT,                     -- kab | kota (regencies only)
    province_code  TEXT NOT NULL
);

-- ----------------------------------------------------------------------- core

-- Dedup cluster: one real job, however many listings describe it.
CREATE TABLE IF NOT EXISTS job_vacancies (
    vacancy_id            TEXT PRIMARY KEY,  -- posting_id of the canonical posting
    canonical_posting_id  TEXT NOT NULL,
    first_seen            TEXT NOT NULL,
    last_seen             TEXT NOT NULL,
    n_postings            INTEGER NOT NULL,
    n_sources             INTEGER NOT NULL,
    is_active             INTEGER NOT NULL DEFAULT 1,
    date_modified         TEXT NOT NULL
);

-- KBLI is coded once per company, not per vacancy.
CREATE TABLE IF NOT EXISTS job_companies (
    company_hmac         TEXT PRIMARY KEY,   -- first 12 hex of HMAC-SHA256(secret, normalised name)
    name_local           TEXT NOT NULL,      -- never exported
    kbli_section         TEXT,               -- A..U
    kbli_model           TEXT,
    kbli_prompt_version  TEXT,
    date_created         TEXT NOT NULL
);

-- One row per listing per source (the clean layer). Contact details are
-- masked before a row is written; the full raw page lives only in the raw
-- store, referenced by raw_key.
CREATE TABLE IF NOT EXISTS job_postings (
    posting_id           TEXT PRIMARY KEY,   -- '<source_id>:<source_job_id>'
    source_id            TEXT NOT NULL REFERENCES job_sources (source_id),
    source_job_id        TEXT NOT NULL,
    url                  TEXT NOT NULL,
    raw_key              TEXT,               -- raw-store key of the page this row was parsed from
    title                TEXT NOT NULL,
    company_name         TEXT,               -- local only; never exported
    company_hmac         TEXT REFERENCES job_companies (company_hmac),
    location_raw         TEXT,
    region_code          TEXT REFERENCES ref_regions (region_code),
    province_code        TEXT,
    is_remote            INTEGER NOT NULL DEFAULT 0,
    salary_raw           TEXT,
    salary_min           INTEGER,            -- monthly IDR after normalisation
    salary_max           INTEGER,
    salary_period        TEXT,               -- as published: month | day | week | year | hour
    education_raw        TEXT,
    education_level      TEXT,
    experience_raw       TEXT,
    experience_years     INTEGER,
    employment_type_raw  TEXT,
    employment_type      TEXT,
    description_masked   TEXT,
    extra_json           TEXT,               -- source-specific fields (JSON)
    content_hash         TEXT,
    posted_at            TEXT,
    valid_through        TEXT,
    first_fetched_at     TEXT NOT NULL,
    fetched_at           TEXT NOT NULL,
    normalised_at        TEXT,
    vacancy_id           TEXT REFERENCES job_vacancies (vacancy_id),
    UNIQUE (source_id, source_job_id)
);
CREATE INDEX IF NOT EXISTS idx_postings_vacancy ON job_postings (vacancy_id);
CREATE INDEX IF NOT EXISTS idx_postings_hash ON job_postings (content_hash);
CREATE INDEX IF NOT EXISTS idx_postings_company_province ON job_postings (company_hmac, province_code);

-- LLM output, versioned: a new model or prompt adds rows instead of
-- overwriting, so results stay comparable across prompt versions.
CREATE TABLE IF NOT EXISTS job_classifications (
    vacancy_id      TEXT NOT NULL REFERENCES job_vacancies (vacancy_id),
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    kbji4           TEXT REFERENCES ref_kbji (code),
    alt4            TEXT REFERENCES ref_kbji (code),
    confidence4     REAL,
    kbji_code       TEXT REFERENCES ref_kbji (code),   -- 6-char jabatan code
    confidence      REAL,
    reason          TEXT,
    needs_review    INTEGER NOT NULL DEFAULT 0,
    pii_found       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,           -- ok | failed | filtered
    date_created    TEXT NOT NULL,
    PRIMARY KEY (vacancy_id, model, prompt_version)
);

CREATE TABLE IF NOT EXISTS job_skills (
    vacancy_id      TEXT NOT NULL REFERENCES job_vacancies (vacancy_id),
    skill_norm      TEXT NOT NULL,
    skill_raw       TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    PRIMARY KEY (vacancy_id, skill_norm)
);
