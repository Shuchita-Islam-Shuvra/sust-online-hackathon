"""HTTP routes for ticket analysis."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..models import AnalyzeRequest, AnalyzeResponse, ErrorResponse
from ..services.gemini import analyze_with_gemini
from ..services.llm import LLMUnavailableError, analyze_with_groq
from ..services.rule_based import analyze_with_rules
from ..services.validate import validate_response


log = logging.getLogger(__name__)
router = APIRouter(tags=["analysis"])


@router.post(
    "/analyze-ticket",
    response_model=AnalyzeResponse,
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def analyze_ticket(req: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    """Analyze one support ticket and return a structured response.

    Pipeline (each layer has its own retry, then falls through on failure):
    1. Gemini  — primary (paid tier, reliable rate limits).
    2. Groq    — secondary (free tier, can 429 under load).
    3. Rules   — deterministic fallback. Response is tagged with
                 ``reason_codes=["fallback:rule_based"]`` so reviewers can see it.

    We only return 500 for programming errors (validation, JSON parse). Any
    LLM-side outage is masked by the next layer so the endpoint never dies.
    """
    settings = request.app.state.settings

    # Layer 1: Gemini
    if settings.gemini_api_key:
        try:
            raw = analyze_with_gemini(settings, req)
            return validate_response(req, raw)
        except LLMUnavailableError as exc:
            log.warning("Gemini unavailable for ticket %s, trying Groq: %s", req.ticket_id, exc)
        except Exception as exc:
            log.exception("Gemini unexpected error for ticket %s", req.ticket_id)

    # Layer 2: Groq
    if settings.groq_api_key:
        try:
            raw = analyze_with_groq(settings, req)
            return validate_response(req, raw)
        except LLMUnavailableError as exc:
            log.warning("Groq unavailable for ticket %s, falling back to rules: %s", req.ticket_id, exc)
        except Exception as exc:
            log.exception("Groq unexpected error for ticket %s", req.ticket_id)

    # Layer 3: rule-based fallback. Always succeeds.
    return analyze_with_rules(req)