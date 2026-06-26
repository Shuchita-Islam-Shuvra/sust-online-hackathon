"""Thin wrapper around the Groq SDK.

Why a wrapper:
- Centralize model selection, retry behavior, and JSON parsing.
- Raise a domain-specific exception so the API layer can decide whether to
  fall back to rule-based analysis or surface a 500.

Retry policy: one retry with lower temperature on transient failures
(timeout, connection error, 5xx, rate limit, empty/malformed output). If the
retry also fails, the caller is expected to invoke the rule-based fallback
so the endpoint never returns 500 for LLM-side issues.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from groq import APITimeoutError, Groq, GroqError

from ..config import Settings
from ..models import AnalyzeRequest
from ..prompts import SYSTEM_PROMPT, build_user_prompt


log = logging.getLogger(__name__)


# Errors that are worth retrying. Authentication errors and malformed output
# are NOT retried — they won't get better on the second try.
_RETRYABLE_GROQ_ERRORS: tuple[type[BaseException], ...] = (
    APITimeoutError,
    GroqError,  # narrowed to status >= 500 / 429 below
)


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM provider cannot be reached or returns unusable output."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, APITimeoutError):
        return True
    if isinstance(exc, GroqError):
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if status is None:
            return False
        return status == 429 or status >= 500
    # Bare network errors from httpx surface as RuntimeError here; retry once.
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Find the first JSON object in a string.

    The model is instructed to return JSON only, but we are defensive against
    occasional leading/trailing prose or stray markdown fences.
    """
    if not raw:
        raise LLMUnavailableError("Empty LLM response")

    text = raw.strip()

    # Strip ```json fences if the model added them anyway.
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMUnavailableError("LLM response contained no JSON object")
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMUnavailableError(f"LLM response was not valid JSON: {exc}") from exc


def _call_once(client: Groq, settings: Settings, req: AnalyzeRequest, *, temperature: float) -> dict[str, Any]:
    completion = client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(req)},
        ],
        temperature=temperature,
        max_tokens=900,
        response_format={"type": "json_object"},
        timeout=settings.request_timeout_seconds,
    )
    if not completion.choices:
        raise LLMUnavailableError("LLM returned no choices")
    raw = completion.choices[0].message.content or ""
    return _extract_json_object(raw)


def analyze_with_groq(settings: Settings, req: AnalyzeRequest) -> dict[str, Any]:
    """Call Groq and return the parsed JSON object from the model.

    Raises ``LLMUnavailableError`` on any provider / parsing failure after
    one retry on transient errors. The API layer catches this and falls
    back to rule-based analysis.
    """
    if not settings.groq_api_key:
        raise LLMUnavailableError("GROQ_API_KEY is not configured")

    client = Groq(api_key=settings.groq_api_key)

    # Attempt 1: temperature 0.1
    try:
        return _call_once(client, settings, req, temperature=0.1)
    except Exception as exc:
        first_exc = exc
        if not _is_retryable(exc):
            log.warning("Groq non-retryable error: %s", exc)
            raise LLMUnavailableError(f"LLM error: {exc.__class__.__name__}") from exc
        log.warning("Groq transient error, retrying with lower temperature: %s", exc)
        # Backoff before the retry. Rate limits (429) need a longer pause
        # than connection / timeout issues.
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        backoff = 3.0 if status == 429 else 0.5
        time.sleep(backoff)

    # Attempt 2: temperature 0.0 (more deterministic, less likely to wander)
    try:
        return _call_once(client, settings, req, temperature=0.0)
    except Exception as exc:
        log.warning("Groq retry also failed: %s", exc)
        raise LLMUnavailableError(
            f"LLM unavailable after retry ({first_exc.__class__.__name__} -> {exc.__class__.__name__})"
        ) from exc