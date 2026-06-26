"""Turn raw LLM output into a validated AnalyzeResponse.

The LLM is instructed to return strict JSON with our enum values, but it can
hallucinate. This module:
- Coerces unknown case_type / department / severity values to safe defaults
- Verifies relevant_transaction_id is actually present in the history
- Caps confidence into [0, 1]
- Runs the safety scrubber on customer_reply and recommended_next_action
- Adds reason_codes for any safety rewrites that fired
"""

from __future__ import annotations

from typing import Any

from ..models import (
    AnalyzeRequest,
    AnalyzeResponse,
    CaseType,
    Department,
    EvidenceVerdict,
    Severity,
)
from ..safety import enforce_safety
from ..prompts import (
    ALLOWED_CASE_TYPES,
    ALLOWED_DEPARTMENTS,
    ALLOWED_SEVERITIES,
    ALLOWED_VERDICTS,
)


# Best-effort mapping from an unknown case_type to a safe department.
_FALLBACK_DEPARTMENT: dict[str, Department] = {
    CaseType.wrong_transfer.value: Department.dispute_resolution,
    CaseType.payment_failed.value: Department.payments_ops,
    CaseType.refund_request.value: Department.customer_support,
    CaseType.duplicate_payment.value: Department.payments_ops,
    CaseType.merchant_settlement_delay.value: Department.merchant_operations,
    CaseType.agent_cash_in_issue.value: Department.agent_operations,
    CaseType.phishing_or_social_engineering.value: Department.fraud_risk,
    CaseType.other.value: Department.customer_support,
}


def _enum(value: Any, allowed: set[str], enum_cls: type, default: Any) -> Any:
    if isinstance(value, str) and value in allowed:
        return enum_cls(value)
    return default


def validate_response(req: AnalyzeRequest, raw: dict[str, Any]) -> AnalyzeResponse:
    """Validate and normalize a parsed LLM JSON object.

    Always returns a populated :class:`AnalyzeResponse`. Any field the LLM
    failed to provide is filled with a safe default.
    """
    history_ids = {tx.transaction_id for tx in req.transaction_history}

    # ----- relevant_transaction_id -----
    rti = raw.get("relevant_transaction_id")
    if isinstance(rti, str) and rti in history_ids:
        relevant_transaction_id: str | None = rti
    elif rti is None:
        relevant_transaction_id = None
    else:
        # The model invented a transaction ID; ignore it.
        relevant_transaction_id = None

    # ----- enums -----
    evidence_verdict = _enum(raw.get("evidence_verdict"), ALLOWED_VERDICTS, EvidenceVerdict, EvidenceVerdict.insufficient_data)

    case_type = _enum(raw.get("case_type"), ALLOWED_CASE_TYPES, CaseType, CaseType.other)
    severity = _enum(raw.get("severity"), ALLOWED_SEVERITIES, Severity, Severity.medium)
    department = _enum(raw.get("department"), ALLOWED_DEPARTMENTS, Department, _FALLBACK_DEPARTMENT[case_type.value])

    # ----- free-text fields -----
    agent_summary = (raw.get("agent_summary") or "").strip() or "Customer submitted a complaint that requires agent review."
    recommended_next_action = (raw.get("recommended_next_action") or "").strip() or "Open the case for agent review and verify the relevant transaction."
    customer_reply = (raw.get("customer_reply") or "").strip() or "Thank you for contacting us. Our team is reviewing your case and will follow up through official channels."

    # ----- safety pass on customer_reply and recommended_next_action -----
    reply_safety = enforce_safety(customer_reply)
    next_action_safety = enforce_safety(recommended_next_action)
    customer_reply = reply_safety.text
    recommended_next_action = next_action_safety.text

    reason_codes: list[str] = []
    if isinstance(raw.get("reason_codes"), list):
        for c in raw["reason_codes"]:
            if isinstance(c, str) and c.strip():
                reason_codes.append(c.strip().lower().replace(" ", "_")[:64])

    # Append safety reasons so the agent sees why text was rewritten.
    for v in reply_safety.violations + next_action_safety.violations:
        reason_codes.append(f"safety_rewrite:{v}")

    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for c in reason_codes:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    reason_codes = deduped

    # ----- human_review_required -----
    hrr_raw = raw.get("human_review_required")
    if isinstance(hrr_raw, bool):
        human_review_required = hrr_raw
    else:
        human_review_required = case_type in {
            CaseType.wrong_transfer,
            CaseType.phishing_or_social_engineering,
        } or evidence_verdict == EvidenceVerdict.insufficient_data

    # For phishing, always require review.
    if case_type == CaseType.phishing_or_social_engineering:
        human_review_required = True

    # ----- confidence -----
    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    return AnalyzeResponse(
        ticket_id=req.ticket_id,
        relevant_transaction_id=relevant_transaction_id,
        evidence_verdict=evidence_verdict,
        case_type=case_type,
        severity=severity,
        department=department,
        agent_summary=agent_summary,
        recommended_next_action=recommended_next_action,
        customer_reply=customer_reply,
        human_review_required=human_review_required,
        confidence=confidence,
        reason_codes=reason_codes,
    )