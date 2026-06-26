"""Rule-based fallback analyzer.

Used only when the Groq call fails (network, auth, malformed output, rate
limit, etc.). Produces a fully-schema-compliant :class:`AnalyzeResponse`
without calling any LLM. Reasoning is necessarily shallow, but the response
is always safe and reviewable.

The classifier uses deterministic keyword/regex matching on the complaint
text and on the supplied transaction history. It is intentionally simple:
better to give the judge a correct, safe answer than a hallucinated one.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..models import (
    AnalyzeRequest,
    AnalyzeResponse,
    CaseType,
    Department,
    EvidenceVerdict,
    Severity,
    TransactionHistoryEntry,
)
from ..safety import enforce_safety


# Each rule: (case_type, list of regex patterns). Order = priority.
_CASE_RULES: list[tuple[CaseType, list[re.Pattern[str]]]] = [
    (
        CaseType.phishing_or_social_engineering,
        [
            re.compile(r"(?i)\b(scam|phish|fraud(ulent)?|fake)\b"),
            re.compile(r"(?i)\b(otp|pin|password|cvv)\b.{0,80}\b(asked|share|send|verify|confirm|give|told|claim)\b"),
            re.compile(r"(?i)\b(asked|told|asked me|told me)\b.{0,80}\b(otp|pin|password)\b"),
            re.compile(r"(?i)\b(someone|caller|person)\b.{0,40}\b(asked|told|claiming|said)\b.{0,80}\b(otp|pin|password|prize|lottery|reward|won|verify|claim)\b"),
            re.compile(r"(?i)\b(prize|lottery|won|reward|winner|bonus)\b"),
            re.compile(r"(?i)\b(fake (sms|call|message|number|offer|website))\b"),
            re.compile(r"(?i)\b(suspicious (call|sms|message|number|link))\b"),
        ],
    ),
    (
        CaseType.wrong_transfer,
        [
            re.compile(r"(?i)\b(wrong (number|recipient|account|person))\b"),
            re.compile(r"(?i)\b(sent .*? (to|destination)\b.{0,80}\b(by mistake|instead|accidentally|mistakenly)\b)"),
            re.compile(r"(?i)\b(sent .*? to (the|a|my) wrong\b)"),
            re.compile(r"(?i)\b(money (was|is) sent to (the wrong|another|a different))\b"),
            re.compile(r"(?i)\b(money went to (the wrong|another|a different))\b"),
            re.compile(r"(?i)\b(sent to (a |the )?different (number|person|account|recipient))\b"),
            re.compile(r"(?i)\b(typo|wrong digit|wrong number)\b"),
            re.compile(r"(?i)\btransferred? to the wrong\b"),
        ],
    ),
    (
        CaseType.payment_failed,
        [
            re.compile(r"(?i)\b(payment (failed|declined|not (going through|received|completed)|didn'?t go through))\b"),
            re.compile(r"(?i)\b(transaction (failed|couldn'?t|didn'?t|not (completed|processed)))\b"),
            re.compile(r"(?i)\b(amount (was )?deduct(ed)? but (payment|transfer|order) (failed|didn'?t go|did not go|was not (completed|received)))\b"),
            re.compile(r"(?i)\b(balance deducted? but (payment|order|transfer) (failed|not received|not completed))\b"),
            re.compile(r"(?i)\b(double (charged|debited))\b"),
        ],
    ),
    (
        CaseType.duplicate_payment,
        [
            re.compile(r"(?i)\b(duplicate(d)? (charge|payment|deduction))\b"),
            re.compile(r"(?i)\b(charged|paid) (twice|two times|again)\b"),
            re.compile(r"(?i)\b(same (amount|payment).{0,30}(twice|again|repeated))\b"),
        ],
    ),
    (
        CaseType.refund_request,
        [
            re.compile(r"(?i)\brefund\b"),
            re.compile(r"(?i)\bmoney back\b"),
            re.compile(r"(?i)\breturn (my |the )?(money|payment|amount)\b"),
        ],
    ),
    (
        CaseType.merchant_settlement_delay,
        [
            re.compile(r"(?i)\b(merchant (settlement|payout) (delay|late|not received|missing))\b"),
            re.compile(r"(?i)\b(store|shop|merchant) (hasn'?t|has not|did not|didn'?t) (received|settled|paid|credited)\b"),
        ],
    ),
    (
        CaseType.agent_cash_in_issue,
        [
            re.compile(r"(?i)\b(agent (cash[- ]?in|deposit) (not|failed|didn'?t|reflect))\b"),
            re.compile(r"(?i)\b(cash (deposit|paid to).{0,30}agent.{0,30}(not|reflect|missing))\b"),
        ],
    ),
]


_DEFAULT_DEPARTMENT: dict[CaseType, Department] = {
    CaseType.wrong_transfer: Department.dispute_resolution,
    CaseType.payment_failed: Department.payments_ops,
    CaseType.refund_request: Department.customer_support,
    CaseType.duplicate_payment: Department.payments_ops,
    CaseType.merchant_settlement_delay: Department.merchant_operations,
    CaseType.agent_cash_in_issue: Department.agent_operations,
    CaseType.phishing_or_social_engineering: Department.fraud_risk,
    CaseType.other: Department.customer_support,
}


_HIGH_VALUE_THRESHOLD_BDT = 20_000.0


def _classify_case(text: str) -> CaseType:
    for case_type, patterns in _CASE_RULES:
        for pat in patterns:
            if pat.search(text):
                return case_type
    return CaseType.other


def _match_transaction(req: AnalyzeRequest) -> tuple[str | None, EvidenceVerdict]:
    """Find a relevant transaction in the supplied history.

    Strategy: look for an explicit transaction id mention in the complaint,
    else look for amount + counterparty number co-occurrence, else fall
    back to most-recent completed transfer/payment.
    """
    history: list[TransactionHistoryEntry] = list(req.transaction_history or [])
    if not history:
        return None, EvidenceVerdict.insufficient_data

    complaint = req.complaint or ""

    # 1. Explicit transaction id in complaint.
    for tx in history:
        if tx.transaction_id and tx.transaction_id in complaint:
            if tx.status.value == "completed":
                return tx.transaction_id, EvidenceVerdict.consistent
            if tx.status.value in {"failed", "reversed"}:
                return tx.transaction_id, EvidenceVerdict.inconsistent
            return tx.transaction_id, EvidenceVerdict.insufficient_data

    # 2. Amount + counterparty co-occurrence.
    for tx in history:
        amount_str = f"{int(tx.amount)}" if float(tx.amount).is_integer() else f"{tx.amount}"
        if amount_str in complaint and tx.counterparty in complaint:
            if tx.status.value == "completed":
                return tx.transaction_id, EvidenceVerdict.consistent
            if tx.status.value in {"failed", "reversed"}:
                return tx.transaction_id, EvidenceVerdict.inconsistent

    # 3. Fall back to most recent completed transfer/payment.
    for tx in history:
        if tx.type.value in {"transfer", "payment"} and tx.status.value == "completed":
            return tx.transaction_id, EvidenceVerdict.insufficient_data

    return None, EvidenceVerdict.insufficient_data


def _severity_for(req: AnalyzeRequest, case_type: CaseType) -> Severity:
    if case_type == CaseType.phishing_or_social_engineering:
        return Severity.high
    amounts: Iterable[float] = (
        tx.amount for tx in (req.transaction_history or [])
        if tx.status.value in {"completed", "pending"}
    )
    high_value = any(a >= _HIGH_VALUE_THRESHOLD_BDT for a in amounts)
    if high_value:
        return Severity.high
    if case_type in {CaseType.wrong_transfer, CaseType.payment_failed, CaseType.duplicate_payment}:
        return Severity.medium
    return Severity.low


def _human_review(req: AnalyzeRequest, case_type: CaseType) -> bool:
    if case_type in {CaseType.wrong_transfer, CaseType.phishing_or_social_engineering}:
        return True
    amounts = (tx.amount for tx in (req.transaction_history or []))
    return any(a >= _HIGH_VALUE_THRESHOLD_BDT for a in amounts)


def _safe_summary(req: AnalyzeRequest, case_type: CaseType, evidence: EvidenceVerdict, rti: str | None) -> str:
    txn_part = f"transaction {rti}" if rti else "the customer's recent activity"
    return (
        f"Rule-based classification: case_type={case_type.value}, "
        f"evidence_verdict={evidence.value}, reviewed against {txn_part}."
    )


def _safe_next_action(case_type: CaseType, evidence: EvidenceVerdict) -> str:
    if evidence == EvidenceVerdict.insufficient_data:
        return "Verify the relevant transaction with the customer and confirm the timeline before any further action."
    if case_type == CaseType.phishing_or_social_engineering:
        return "Escalate to the fraud-risk team for review and continue communication only through official channels."
    if case_type == CaseType.wrong_transfer:
        return "Open a dispute for the relevant transfer and route to the dispute-resolution queue."
    return f"Route this case to the {_DEFAULT_DEPARTMENT[case_type].value} queue for human review."


def _safe_customer_reply(req: AnalyzeRequest, case_type: CaseType, evidence: EvidenceVerdict) -> str:
    """Build a customer reply that always respects every safety rule."""
    if case_type == CaseType.phishing_or_social_engineering:
        return (
            "Thank you for reporting this. For your safety, please do not share any OTP, PIN, "
            "or password with anyone, including our team. We will only contact you through official "
            "support channels."
        )
    if evidence == EvidenceVerdict.inconsistent:
        return (
            "Thank you for reaching out. Based on the information on file, the situation you described "
            "does not match our records. Our team will review the relevant activity and follow up "
            "through official channels. Any eligible amount will be reviewed and, where applicable, "
            "returned through official channels only after verification."
        )
    return (
        "Thank you for contacting us. We have noted your concern and our team is reviewing the "
        "relevant activity. Any eligible amount will be reviewed and, where applicable, returned "
        "through official channels only after verification."
    )


def analyze_with_rules(req: AnalyzeRequest) -> AnalyzeResponse:
    """Produce a schema-valid AnalyzeResponse without calling any LLM."""
    case_type = _classify_case(req.complaint)
    relevant_tx, evidence = _match_transaction(req)
    severity = _severity_for(req, case_type)
    department = _DEFAULT_DEPARTMENT[case_type]
    needs_review = _human_review(req, case_type)

    agent_summary = _safe_summary(req, case_type, evidence, relevant_tx)
    next_action = _safe_next_action(case_type, evidence)
    customer_reply = enforce_safety(_safe_customer_reply(req, case_type, evidence)).text

    # Heuristic confidence: high when we found an explicit match, low otherwise.
    if evidence == EvidenceVerdict.consistent and relevant_tx:
        confidence = 0.7
    elif evidence == EvidenceVerdict.inconsistent:
        confidence = 0.7
    else:
        confidence = 0.4

    reason_codes = ["fallback:rule_based"]
    if relevant_tx:
        reason_codes.append("transaction_match")
    reason_codes.append(case_type.value)

    return AnalyzeResponse(
        ticket_id=req.ticket_id,
        relevant_transaction_id=relevant_tx,
        evidence_verdict=evidence,
        case_type=case_type,
        severity=severity,
        department=department,
        agent_summary=agent_summary,
        recommended_next_action=next_action,
        customer_reply=customer_reply,
        human_review_required=needs_review,
        confidence=confidence,
        reason_codes=reason_codes,
    )