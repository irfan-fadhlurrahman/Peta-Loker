"""BytePlus ModelArk client (OpenAI-compatible chat completions), used for
KBJI/KBLI coding and skill extraction. Model: Seed 2.0 Pro (ARK_MODEL).

Credentials come from .env (see core/env.py): ARK_API_KEY, ARK_BASE_URL,
ARK_MODEL. Called with raw httpx — the same pattern as the social-media
pipeline's client.

Behaviour worth knowing:
  * temperature 0; the caller validates every response against the
    reference tables, so a malformed answer is never trusted;
  * Seed 2.0 is a reasoning model: if it runs out of tokens while reasoning,
    the HTTP call succeeds but `content` is empty (finish_reason="length").
    That, 429/5xx and timeouts are retried with exponential backoff;
  * thinking is set from config (llm.thinking); off by default because
    reasoning made batches 10-20x slower;
  * finish_reason="content_filter" is deterministic, so it is raised as
    ContentFilterError straight away — the caller bisects the batch to
    isolate the offending item instead of retrying.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass

import httpx

import core.env  # noqa: F401  (import for side effect: loads .env)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 5
MAX_TOKENS = 32_768
TIMEOUT_SECONDS = 180.0


class LlmError(Exception):
    """No usable answer after all retries, or an answer that isn't valid JSON."""


class ContentFilterError(LlmError):
    """The provider's moderation rejected the request. Not retried."""


class BadResponse(LlmError):
    """The model answered, but not in the required shape (not JSON, wrong
    refs, unknown codes). Worth one more try; the model is non-deterministic
    enough at the margins that a retry often succeeds."""


@dataclass
class LlmResponse:
    content: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class LlmClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep, thinking: str | None = None):
        self.api_key = api_key or os.environ.get("ARK_API_KEY", "")
        self.base_url = (base_url or os.environ.get("ARK_BASE_URL", "")).rstrip("/")
        self.model = model or os.environ.get("ARK_MODEL", "")
        if not (self.api_key and self.base_url and self.model):
            raise RuntimeError("ARK_API_KEY, ARK_BASE_URL and ARK_MODEL must be set (see .env.example)")
        self._client = httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport)
        self._sleep = sleep
        self.thinking = thinking  # "enabled" / "disabled"; None = the model's default

    def close(self) -> None:
        self._client.close()

    def _call(self, system: str, user: str) -> LlmResponse:
        started = time.monotonic()
        response = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0,
                "max_tokens": MAX_TOKENS,
                **({"thinking": {"type": self.thinking}} if self.thinking else {}),
            },
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code == 429 or response.status_code >= 500:
            raise LlmError(f"HTTP {response.status_code}")
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = body.get("usage") or {}
        if not content:
            if choice.get("finish_reason") == "content_filter":
                raise ContentFilterError("content_filter rejected the request")
            raise LlmError(f"empty content (finish_reason={choice.get('finish_reason')!r}, usage={usage!r})")
        return LlmResponse(content, usage.get("prompt_tokens"), usage.get("completion_tokens"), latency_ms)

    def complete(self, system: str, user: str) -> LlmResponse:
        """One chat completion with retries on transient failures."""
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._call(system, user)
            except ContentFilterError:
                raise
            except (LlmError, httpx.TimeoutException, httpx.TransportError) as e:
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                    logger.warning("LLM attempt %d/%d failed (%s), retrying in %ds", attempt, MAX_ATTEMPTS, e, delay)
                    self._sleep(delay)
        raise LlmError(f"no usable response after {MAX_ATTEMPTS} attempts: {last_error}")


def strip_code_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return match.group(1) if match else text


def parse_json_array(content: str) -> list:
    try:
        data = json.loads(strip_code_fences(content))
    except json.JSONDecodeError as e:
        raise BadResponse(f"response is not valid JSON: {e}; content starts {content[:200]!r}") from e
    if isinstance(data, dict):  # some models wrap the array: {"results": [...]}
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if len(lists) == 1 else data
    if not isinstance(data, list):
        raise BadResponse(f"expected a JSON array, got {type(data).__name__}")
    return data
