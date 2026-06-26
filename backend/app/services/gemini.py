"""Thin wrapper around the Google Gemini API.

Mirrors ``services/llm.py`` but for Gemini. Used as the *primary* LLM
provider because the project's paid Gemini tier has much higher rate limits
than the free Groq tier (which 429s during heavy burst testing).

Same retry policy: one retry with lower temperature on transient errors,
then propagate ``LLMUnavailableError`` so the router can fall back to the
next provider (Groq, then rules).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from google import genai
from google.genai import errors as genai_errors

from ..config import Settings
from ..models import AnalyzeRequest
from ..prompts import SYSTEM_PROMPT, build_user_prompt
from .llm import LLMUnavailableError, _extract_json_object


log = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    # genai raises APIError subclasses with .code (HTTP status) on HTTP failures.
    status = getattr(exc, "code", None)
    if status is None:
        return isinstance(exc, (ConnectionError, TimeoutError, OSError))
    return status == 429 or status >= 500


def _call_once(client: genai.Client, settings: Settings, req: AnalyzeRequest, *, temperature: float) -> dict[str, Any]:
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=build_user_prompt(req),
        config={
            "system_instruction": SYSTEM_PROMPT,
            "response_mime_type": "application/json",
            "temperature": temperature,
            "max_output_tokens": 1500,
        },
    )
    raw = (response.text or "").strip()
    if not raw:
        raise LLMUnavailableError("Gemini returned empty response")
    return _extract_json_object(raw)


def analyze_with_gemini(settings: Settings, req: AnalyzeRequest) -> dict[str, Any]:
    """Call Gemini and return the parsed JSON object from the model.

    Raises ``LLMUnavailableError`` after one retry on transient errors. The
    API layer catches this and falls back to the next provider (Groq, then
    rule-based).
    """
    if not settings.gemini_api_key:
        raise LLMUnavailableError("GEMINI_API_KEY is not configured")

    client = genai.Client(api_key=settings.gemini_api_key)

    try:
        return _call_once(client, settings, req, temperature=0.1)
    except Exception as exc:
        first_exc = exc
        if not _is_retryable(exc):
            log.warning("Gemini non-retryable error: %s", exc)
            raise LLMUnavailableError(f"Gemini error: {exc.__class__.__name__}") from exc
        log.warning("Gemini transient error, retrying with lower temperature: %s", exc)
        status = getattr(exc, "code", None)
        backoff = 3.0 if status == 429 else 0.5
        time.sleep(backoff)

    try:
        return _call_once(client, settings, req, temperature=0.0)
    except Exception as exc:
        log.warning("Gemini retry also failed: %s", exc)
        raise LLMUnavailableError(
            f"Gemini unavailable after retry ({first_exc.__class__.__name__} -> {exc.__class__.__name__})"
        ) from exc