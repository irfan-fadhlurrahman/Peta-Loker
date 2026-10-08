"""LLM client, validation and the enrich flow — against a fake Ark endpoint
(httpx.MockTransport). No API key or network needed."""

from __future__ import annotations

import json

import httpx
import pytest

from core import db
from core.classify import Taxonomy, run_batch, validate_kbli, validate_step_a, validate_step_b
from core.llm_client import BadResponse, ContentFilterError, LlmClient, LlmError, parse_json_array
from scripts.enrich import Recorder, classify_companies, classify_vacancies

SETTINGS = {"batch_size": 2, "review_threshold": 0.7, "description_chars": 800, "max_skills": 10}


@pytest.fixture(scope="module")
def tax():
    return Taxonomy.from_reference()


def _reply(content="", finish="stop", status=200):
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 50}}
    return httpx.Response(status, json=body)


def _client(handler) -> LlmClient:
    return LlmClient(api_key="k", base_url="https://ark.test/api/v3", model="seed-2-0-lite-test",
                     transport=httpx.MockTransport(handler), sleep=lambda s: None)


# ------------------------------------------------------------------- client


def test_client_retries_empty_content_then_succeeds():
    replies = iter([_reply("", "length"), _reply(status=503), _reply('[{"ref": "j1"}]')])
    client = _client(lambda r: next(replies))
    response = client.complete("sys", "user")
    assert response.content == '[{"ref": "j1"}]' and response.input_tokens == 1000


def test_client_raises_content_filter_without_retrying():
    calls = []

    def handler(request):
        calls.append(1)
        return _reply("", "content_filter")

    with pytest.raises(ContentFilterError):
        _client(handler).complete("s", "u")
    assert len(calls) == 1


def test_client_gives_up_after_max_attempts():
    with pytest.raises(LlmError):
        _client(lambda r: _reply(status=500)).complete("s", "u")


def test_client_sends_thinking_only_when_configured():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _reply("[]")

    _client(handler).complete("s", "u")
    off = LlmClient(api_key="k", base_url="https://ark.test/api/v3", model="m", thinking="disabled",
                    transport=httpx.MockTransport(handler), sleep=lambda s: None)
    off.complete("s", "u")
    assert "thinking" not in bodies[0]
    assert bodies[1]["thinking"] == {"type": "disabled"}


def test_client_requires_credentials(monkeypatch):
    for var in ("ARK_API_KEY", "ARK_BASE_URL", "ARK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError):
        LlmClient()


def test_parse_json_array_handles_fences_and_wrappers():
    assert parse_json_array('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert parse_json_array('{"results": [{"a": 1}]}') == [{"a": 1}]
    with pytest.raises(BadResponse):
        parse_json_array("not json")


# --------------------------------------------------------------- validation


def test_step_a_validation(tax):
    ok = [{"ref": "j1", "kbji4": "2511", "alt4": "9999", "confidence": 0.9, "skills": ["SQL", " ", "Python"],
           "pii_found": False}]
    out = validate_step_a(["j1"], ok, tax, max_skills=10)
    assert out["j1"] == {"kbji4": "2511", "alt4": None, "confidence4": 0.9, "skills": ["SQL", "Python"],
                         "pii_found": False}
    with pytest.raises(BadResponse):
        validate_step_a(["j1"], [{**ok[0], "kbji4": "9999"}], tax, 10)       # unknown code
    with pytest.raises(BadResponse):
        validate_step_a(["j1", "j2"], ok, tax, 10)                           # missing ref
    with pytest.raises(BadResponse):
        validate_step_a(["j1"], [{**ok[0], "confidence": 1.5}], tax, 10)     # out of range


def test_step_b_requires_a_child_of_the_unit(tax):
    good = [{"ref": "j1", "kbji_code": "2512.03", "confidence": 0.8, "reason": "menulis kode"}]
    assert validate_step_b(["j1"], good, tax, "2512")["j1"]["kbji_code"] == "2512.03"
    with pytest.raises(BadResponse):
        validate_step_b(["j1"], [{**good[0], "kbji_code": "2511.01"}], tax, "2512")


def test_kbli_section_must_be_a_to_u(tax):
    assert validate_kbli(["c1"], [{"ref": "c1", "kbli_section": "j", "confidence": 0.7}], tax)["c1"][
        "kbli_section"] == "J"
    with pytest.raises(BadResponse):
        validate_kbli(["c1"], [{"ref": "c1", "kbli_section": "Z", "confidence": 0.7}], tax)


def test_run_batch_bisects_to_isolate_a_filtered_item(tax):
    def handler(request):
        user = json.loads(request.content)["messages"][1]["content"]
        items = json.loads(user)
        if any("TERLARANG" in it["title"] for it in items):
            return _reply("", "content_filter")
        return _reply(json.dumps([{"ref": it["ref"], "kbji4": "2511", "alt4": None, "confidence": 0.9,
                                   "skills": [], "pii_found": False} for it in items]))

    items = [{"ref": f"j{i}", "title": "TERLARANG" if i == 3 else "Analis"} for i in range(1, 5)]
    calls = []
    results, bad = run_batch(_client(handler), "kbji4", "sys", items,
                             lambda refs, res: validate_step_a(refs, res, tax, 10), calls.append)
    assert sorted(results) == ["j1", "j2", "j4"] and bad == ["j3"]
    assert [c.status for c in calls].count("filtered") >= 2


# ------------------------------------------------------------------- enrich


def fake_ark(request):
    """Answers each step validly, choosing codes from the prompt itself."""
    payload = json.loads(request.content)
    system, items = payload["messages"][0]["content"], json.loads(payload["messages"][1]["content"])
    if "KBLI 2020" in system:
        result = [{"ref": it["ref"], "kbli_section": "J", "confidence": 0.8} for it in items]
    elif "Choose the single best 6-digit" in system:
        code = next(line.split()[0] for line in system.splitlines() if line[:4].isdigit() and "." in line[:7])
        result = [{"ref": it["ref"], "kbji_code": code, "confidence": 0.9, "reason": "cocok"} for it in items]
    else:
        result = [{"ref": it["ref"], "kbji4": "2512" if "Engineer" in it["title"] else "4110", "alt4": "2511",
                   "confidence": 0.95 if "Engineer" in it["title"] else 0.5, "skills": ["Python", "python ", "SQL"],
                   "pii_found": False} for it in items]
    return _reply(json.dumps(result))


def _seed(conn):
    db.upsert_source(conn, "glints", "x", None)
    db.upsert_company(conn, "abc123", "PT Contoh")
    for pid, title in (("glints:1", "Software Engineer"), ("glints:2", "Staf Administrasi"),
                       ("glints:3", "Backend Engineer")):
        db.upsert_postings(conn, [{"posting_id": pid, "source_id": "glints", "source_job_id": pid[-1],
                                   "url": "u", "title": title, "company_name": "PT Contoh", "company_hmac": "abc123",
                                   "description_masked": "deskripsi", "fetched_at": "2026-10-08T09:00:00+07:00"}])
        conn.execute("INSERT INTO job_vacancies VALUES (?, ?, '2026-10-01', '2026-10-08', 1, 1, 1, 'x')", (pid, pid))
    conn.commit()


def test_enrich_end_to_end_with_fake_model(conn_with_reference, tax):
    conn = conn_with_reference
    _seed(conn)
    client = _client(fake_ark)
    record = Recorder(conn, client.model)
    rows = db.vacancies_to_classify(conn, client.model, "v1")
    stats = classify_vacancies(conn, client, tax, rows, SETTINGS, record)
    assert stats == {"vacancies": 3, "ok": 3, "partial": 0, "failed": 0, "needs_review": 1}
    row = conn.execute("SELECT * FROM job_classifications WHERE vacancy_id = 'glints:1'").fetchone()
    assert row["kbji4"] == "2512" and row["kbji_code"].startswith("2512.") and row["needs_review"] == 0
    skills = [r[0] for r in conn.execute("SELECT skill_norm FROM job_skills WHERE vacancy_id = 'glints:1'")]
    assert sorted(skills) == ["python", "sql"]  # "Python" and "python " collapse to one
    assert db.count(conn, "llm_usage", "status = 'ok'") == record.totals["calls_ok"] > 0

    # Nothing left to do for this model and prompt version.
    assert db.vacancies_to_classify(conn, client.model, "v1") == []

    assert classify_companies(conn, client, tax, SETTINGS, record) == {"companies": 1, "coded": 1}
    assert conn.execute("SELECT kbli_section FROM job_companies").fetchone()[0] == "J"
