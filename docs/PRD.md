# Peta Loker — product requirements

*Portfolio project · v1 · October 2026*

## 1. Goal

Turn scattered Indonesian online job ads into a clean, standardised, de-duplicated dataset of labour
demand, coded to **KBJI 2026** occupations and **BPS** regions, and show it in a public dashboard that
anyone can read and check.

The project also demonstrates, end to end:
- data engineering: a multi-source collection framework, raw storage, portable SQL, idempotent re-runs;
- applied LLM work with **measured** accuracy, not claimed accuracy;
- data-quality gates that decide what may be published;
- responsible handling of scraped data: masking, pseudonymised companies, documented source terms;
- product thinking (this document) and delivery (tests, docs, a one-command demo, a live snapshot).

## 2. Problem

Online job ads are the fastest signal of which jobs, skills and regions are hiring — but as raw data they
are hard to use:

| # | Problem | Consequence |
|---|---|---|
| P1 | **No continuity.** Collection breaks when a site changes its layout or blocks access | Gaps in the time series |
| P2 | **Missing variables.** Salary, education and experience are often absent or buried in free text | Weak analysis by wage, education or seniority |
| P3 | **No standardisation.** "Jaksel" vs "Jakarta Selatan"; "5-7 jt" vs "Rp5.000.000" | Sources can't be aggregated or compared |
| P4 | **Duplicates.** One job is posted on several portals and reposted over time | Demand is over-counted |
| P5 | **No official coding.** Job titles aren't mapped to KBJI, employers aren't mapped to KBLI | Can't be compared with official statistics or policy categories |
| P6 | **Manual work.** Cleaning and coding by hand | Slow, inconsistent, doesn't scale |
| P7 | **Fragmented sources.** Each portal has its own format | No single source of truth |

## 3. Users

| User | Needs |
|---|---|
| Labour-market analyst / researcher | Demand by occupation, region, skill, wage and education; coding they can trust or check |
| Job seeker / career counsellor | Which skills and regions are in demand, and typical pay by education |
| Data or hiring manager reviewing this portfolio | Evidence of engineering quality, honest metrics, responsible data handling |

## 4. Scope

**In scope**
- Five live sources (Glints, Dealls, Loker.id, Kalibrr, KitaLulus) plus file import; postings from the last
  60 days.
- Normalisation of salary (monthly IDR), education level, experience, employment type and location (BPS
  regency codes).
- Three-pass dedup into stable vacancy ids.
- Two-step LLM coding to KBJI 2026 (4-digit, then 6-digit), KBLI section per company, skill extraction.
- Quality gate, public dashboard (overview, vacancy list, operations), guarded deploy, offline demo.

**Non-goals**
- Official statistics or policy advice.
- Production operation, logins, user roles, a public API.
- More than five sources; coverage of offline or informal hiring.
- Training models.

## 5. Requirements

| # | Requirement | Answers |
|---|---|---|
| R1 | Every source is a subclass of one base class; robots.txt, delays, page caps, masking and the age window are enforced in the base class, not per source | P1, P7 |
| R2 | Every fetched page is kept in the raw store so parsers can be fixed and re-run without fetching again | P1 |
| R3 | Structured data (JSON-LD, embedded JSON) is preferred over HTML scraping | P2 |
| R4 | Missing values stay missing — nothing is imputed | P2 |
| R5 | Salary, education, experience, employment type and location are normalised to fixed vocabularies and BPS codes | P3 |
| R6 | Reposts and cross-portal copies are merged into one vacancy with a stable id | P4 |
| R7 | Every vacancy gets a KBJI 2026 code with a confidence and reason; every answer is validated against the reference table | P5 |
| R8 | One command runs the whole pipeline; a quality gate decides whether output may be published | P6 |
| R9 | Contact details are removed before storage; companies are pseudonymised in everything published | Responsible use |
| R10 | A fresh clone runs an offline demo with no accounts | Delivery |

## 6. Success metrics

| Metric | Target | Where it's reported |
|---|---|---|
| Sources collected successfully | 5 of 5, over at least 2 weeks | Operations page |
| Location mapped to a BPS region | ≥ 90% (regency level reported separately) | Quality gate, `docs/results.md` |
| Salary parsed where a salary is shown | ≥ 35% of postings | Quality gate |
| Dedup precision on checked pairs (by hand or an independent LLM reviewer) | ≥ 95% | `docs/results.md` |
| KBJI accuracy (or LLM-reviewer agreement) on 200 labelled vacancies | reported at 1, 2, 4 and 6 digits | `docs/results.md` |
| LLM cost per 1,000 vacancies | reported | `docs/results.md` |
| Contact details in published output | 0 | Quality gate + deploy check |
| `make demo` from a fresh clone | < 5 minutes | README |

## 7. Limitations

- **Online ads are not the labour market.** Formal, urban, white-collar jobs are over-represented; informal
  and blue-collar hiring (often by word of mouth or social media) is under-represented. KitaLulus is
  included partly to widen coverage of frontline work, but the bias remains.
- **Five portals are a sample**, not a census; shares between occupations depend on which portals are used.
- **Sources change without notice.** A layout change can stop a source; the run log and quality gate make
  that visible, and saved raw pages allow a re-parse once fixed.
- **LLM coding makes mistakes.** Coding is checked against a labelled sample (currently an independent LLM reviewer) and low-confidence codes are
  flagged, but individual codes can be wrong.
- **Ambiguous place names** ("Tangerang", "Bandung" without "Kota"/"Kab.") are coded to the province only.
- **Terms of service.** Several sources' terms restrict copying or automated collection; see
  [terms_of_service.md](terms_of_service.md) for what each says and how the project limits its use.

## 8. Future work

- Docker and an orchestrator for unattended daily runs; PostgreSQL in place of SQLite.
- More sources — official APIs and data-sharing agreements first, then structured data — and social-media
  job accounts for informal work.
- MinHash/LSH dedup and company entity resolution at larger scale.
- A larger evaluation set with double coding, a review interface, and a distilled in-house classifier
  trained on reviewed labels.
- A standard skills taxonomy instead of free-text skills.
