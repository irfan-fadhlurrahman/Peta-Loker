"""KBJI 2026 occupation coding, KBLI 2020 industry coding and skill
extraction with an LLM, validated against the reference tables.

Two-step KBJI coding keeps prompts small: 2,380 job titles don't fit in one
prompt, but the 447 four-digit unit groups do.
  Step A (batch of vacancies): pick the 4-digit unit group from the full list
          (an identical prefix in every call, so it caches well), plus a
          second choice, a confidence, the skills asked for, and a flag if
          the text still contains contact details.
  Step B (per unit group): pick the 6-digit jabatan among that unit's
          children only, with a confidence and a one-line reason.
KBLI is coded once per company (section A-U), not per vacancy.

Every answer is checked in code: references must match the batch, codes must
exist in the reference table, a 6-digit code must sit under its 4-digit
parent, confidences must be in [0, 1]. A batch that fails validation is
retried once and then marked failed — never partially trusted.

Items are sent with short references ("j1", "j2", …) instead of real ids:
models occasionally miscopy long opaque ids.
"""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.llm_client import BadResponse, ContentFilterError, LlmClient, LlmError, LlmResponse, parse_json_array

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "v1"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- reference


@dataclass
class Taxonomy:
    units: dict[str, str]                 # 4-digit code -> title
    jabatan: dict[str, str]               # 'NNNN.NN' -> title
    children: dict[str, list[str]]        # 4-digit code -> its jabatan codes
    kbli: dict[str, str]                  # section letter -> title

    @classmethod
    def from_reference(cls) -> Taxonomy:
        units, jabatan, children = {}, {}, {}
        with (REPO_ROOT / "reference" / "kbji_2026.csv").open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["level"] == "4":
                    units[row["code"]] = row["title"]
                elif row["level"] == "5":
                    jabatan[row["code"]] = row["title"]
                    children.setdefault(row["parent_code"], []).append(row["code"])
        with (REPO_ROOT / "reference" / "kbli_sections.csv").open(encoding="utf-8", newline="") as f:
            kbli = {row["section"]: row["title"] for row in csv.DictReader(f)}
        return cls(units, jabatan, children, kbli)


# ------------------------------------------------------------------ prompts

STEP_A_RULES = """You code Indonesian online job vacancies to KBJI 2026 (Klasifikasi Baku Jabatan Indonesia,
BPS), which follows ISCO-08. For each vacancy, choose the single best 4-digit unit group (subgolongan) from
the list below, judging by the actual tasks and level of the job, not by keywords in the title alone.
Seniority matters: a manager who leads a function belongs in major group 1, a supervisor of workers usually
stays in the workers' group, and sales or admin trainees belong with the work they do.

Also extract up to {max_skills} concrete skills the vacancy asks for (tools, techniques, licences, languages;
short Indonesian or English noun phrases as written; not personality traits like "jujur" or "teliti").
Set pii_found to true if the text still contains a phone number, e-mail address or a person's name to contact.

Reply with ONLY a JSON array, one object per vacancy, in the same order:
[{{"ref": "j1", "kbji4": "2511", "alt4": "2519", "confidence": 0.86, "skills": ["SQL", "Python"], "pii_found": false}}]
- kbji4 and alt4 must be codes from the list; alt4 is your second choice (or null).
- confidence is your probability (0-1) that kbji4 is correct.

KBJI 2026 unit groups (code title):
{units}"""

STEP_B_RULES = """You refine KBJI 2026 occupation codes. Every vacancy below has already been placed in unit group
{unit} "{unit_title}". Choose the single best 6-digit jabatan code for each vacancy from this list only:
{children}

Reply with ONLY a JSON array, one object per vacancy, in the same order:
[{{"ref": "j1", "kbji_code": "{example}", "confidence": 0.8, "reason": "one short sentence"}}]
- kbji_code must be one of the codes listed above.
- If none fits well, pick the closest (often the 'Lainnya' code ending in .99) and give a low confidence."""

KBLI_RULES = """You assign Indonesian companies to a KBLI 2020 section (A-U) — the industry the company itself
operates in, not the occupation of the job advertised. Use the company name, the industry hint and the jobs
it advertises. Sections:
{sections}

Reply with ONLY a JSON array, one object per company, in the same order:
[{{"ref": "c1", "kbli_section": "J", "confidence": 0.7}}]"""


def step_a_system(tax: Taxonomy, max_skills: int) -> str:
    units = "\n".join(f"{code} {title}" for code, title in sorted(tax.units.items()))
    return STEP_A_RULES.format(max_skills=max_skills, units=units)


def step_b_system(tax: Taxonomy, unit: str) -> str:
    children = "\n".join(f"{code} {tax.jabatan[code]}" for code in tax.children.get(unit, []))
    example = tax.children.get(unit, [f"{unit}.01"])[0]
    return STEP_B_RULES.format(unit=unit, unit_title=tax.units.get(unit, ""), children=children, example=example)


def kbli_system(tax: Taxonomy) -> str:
    return KBLI_RULES.format(sections="\n".join(f"{k} {v}" for k, v in sorted(tax.kbli.items())))


def items_prompt(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False, indent=1)


# --------------------------------------------------------------- validation


class ValidationError(BadResponse):
    """The answer doesn't fit the batch or the reference tables."""


def _confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"confidence {value!r} is not a number") from None
    if not 0 <= number <= 1:
        raise ValidationError(f"confidence {number} outside [0, 1]")
    return number


def _check_refs(refs: list[str], results: list) -> dict[str, dict]:
    if not all(isinstance(r, dict) for r in results):
        raise ValidationError("every result must be an object")
    got = [r.get("ref") for r in results]
    if sorted(got) != sorted(refs):
        raise ValidationError(f"refs {got} don't match the batch {refs}")
    return {r["ref"]: r for r in results}


def validate_step_a(refs: list[str], results: list, tax: Taxonomy, max_skills: int) -> dict[str, dict]:
    by_ref = _check_refs(refs, results)
    out = {}
    for ref, r in by_ref.items():
        kbji4, alt4 = str(r.get("kbji4") or ""), r.get("alt4")
        if kbji4 not in tax.units:
            raise ValidationError(f"{ref}: kbji4 {kbji4!r} is not a KBJI 2026 unit group")
        alt4 = str(alt4) if alt4 not in (None, "", "null") else None
        if alt4 is not None and alt4 not in tax.units:
            alt4 = None  # a bad second choice isn't worth failing the batch
        skills = r.get("skills") or []
        if not isinstance(skills, list):
            raise ValidationError(f"{ref}: skills must be a list")
        skills = [str(s).strip() for s in skills if str(s).strip()][:max_skills]
        out[ref] = {"kbji4": kbji4, "alt4": alt4, "confidence4": _confidence(r.get("confidence")),
                    "skills": skills, "pii_found": bool(r.get("pii_found"))}
    return out


def validate_step_b(refs: list[str], results: list, tax: Taxonomy, unit: str) -> dict[str, dict]:
    by_ref = _check_refs(refs, results)
    out = {}
    for ref, r in by_ref.items():
        code = str(r.get("kbji_code") or "")
        if code not in tax.jabatan or not code.startswith(unit + "."):
            raise ValidationError(f"{ref}: kbji_code {code!r} is not a jabatan under {unit}")
        out[ref] = {"kbji_code": code, "confidence": _confidence(r.get("confidence")),
                    "reason": str(r.get("reason") or "")[:300]}
    return out


def validate_kbli(refs: list[str], results: list, tax: Taxonomy) -> dict[str, dict]:
    by_ref = _check_refs(refs, results)
    out = {}
    for ref, r in by_ref.items():
        section = str(r.get("kbli_section") or "").strip().upper()
        if section not in tax.kbli:
            raise ValidationError(f"{ref}: kbli_section {section!r} is not A-U")
        out[ref] = {"kbli_section": section, "confidence": _confidence(r.get("confidence"))}
    return out


# ------------------------------------------------------------------ calling


@dataclass
class CallRecord:
    step: str
    n_items: int
    status: str
    response: LlmResponse | None = None


def run_batch(client: LlmClient, step: str, system: str, items: list[dict], validate: Callable,
              record: Callable[[CallRecord], None]) -> tuple[dict[str, dict], list[str]]:
    """Call the model for `items` (each with a 'ref'), validate, retry once on
    a validation failure, and bisect on a content-filter rejection. Returns
    (results by ref, refs that failed or were filtered)."""
    refs = [item["ref"] for item in items]
    for attempt in (1, 2):
        try:
            response = client.complete(system, items_prompt(items))
            results = validate(refs, parse_json_array(response.content))
            record(CallRecord(step, len(items), "ok", response))
            return results, []
        except ContentFilterError:
            record(CallRecord(step, len(items), "filtered"))
            if len(items) == 1:
                return {}, refs
            half = len(items) // 2
            left, bad_left = run_batch(client, step, system, items[:half], validate, record)
            right, bad_right = run_batch(client, step, system, items[half:], validate, record)
            return {**left, **right}, bad_left + bad_right
        except BadResponse as e:
            logger.warning("%s batch of %d: bad answer (attempt %d): %s", step, len(items), attempt, e)
            record(CallRecord(step, len(items), "failed"))
        except LlmError as e:  # the client already retried transient failures
            logger.error("%s batch of %d failed: %s", step, len(items), e)
            record(CallRecord(step, len(items), "failed"))
            break
    return {}, refs
