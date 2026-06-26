import re
from typing import List, Tuple, Optional
from app.schemas import (
    AnalyzeTicketRequest,
    AnalyzeTicketResponse,
    CaseTypeEnum,
    DepartmentEnum,
    SeverityEnum,
    EvidenceVerdictEnum
)

# --- Safety Scan Patterns ---

CREDENTIAL_PATTERNS = [
    # Requesting credentials
    r"(?i)\b(ask|give|share|send|provide|tell|enter|input|verify|confirm|credentials)\b.*\b(otp|pin|password|cvv|card\s*(number|no)|passcode)\b",
    r"(?i)\b(otp|pin|password|cvv|card\s*(number|no)|passcode)\b.*\b(ask|give|share|send|provide|tell|enter|input|verify|confirm|credentials)\b",
    r"(?i)request.*\b(otp|pin|password|cvv|card\s*(number|no)|passcode)\b",
    r"(?i)\b(otp|pin|password|cvv)\b.*\b(needed|required|verify)\b"
]

REFUND_PROMISE_PATTERNS = [
    # Unauthorized promises of refunds/reimbursements
    r"(?i)\bwe\s+(will|shall|have|are\s+going\s+to)\s+(refund|reimburse|return\s+your\s+money|send\s+your\s+money\s+back)\b",
    r"(?i)\byour\s+money\s+(will|shall|has)\s+(be\s+refunded|be\s+returned|be\s+credited|been\s+refunded|been\s+returned)\b",
    r"(?i)\brefund\s+is\s+(processed|approved|done|initiated|credited)\b",
    r"(?i)\bwill\s+get\s+your\s+money\s+back\b",
    r"(?i)\bpromise\s+a\s+refund\b"
]

THIRD_PARTY_PATTERNS = [
    # Reference to contact on Telegram, WhatsApp, Viber, etc.
    r"(?i)\b(whatsapp|telegram|signal|skype|viber|facebook|messenger|instagram|insta|wechat)\b",
    r"(?i)\b(contact|message|call|email|reach\s+out)\s+(us\s+)?(at|on|via|through)\s+(\+?[0-9\-\s]{8,15}|[a-zA-Z0-9._%+-]+@gmail\.com|[a-zA-Z0-9.-]+\.external)\b"
]

# --- Safe Templates ---

SAFE_CREDENTIAL_REPLY = (
    "Dear customer, for your security, we will never ask for your PIN, OTP, password, CVV, "
    "or full card number. Please do not share these details with anyone. We are reviewing your "
    "case and will update you shortly."
)
SAFE_CREDENTIAL_ACTION = (
    "Potential security risk or phishing attempt. Do not request sensitive credentials from the "
    "customer. Escalating for manual review."
)

SAFE_REFUND_REPLY = (
    "We have received your dispute request and are investigating the transaction. Any refund or "
    "adjustment is subject to our terms and merchant policy. We will notify you as soon as the "
    "investigation is complete."
)
SAFE_REFUND_ACTION = (
    "Investigate transaction history and process dispute through standard channels. Avoid making "
    "preemptive refund promises."
)

SAFE_THIRD_PARTY_REPLY = (
    "Please keep all communication within our official app support channels. We will update you "
    "here once we have resolved your issue."
)
SAFE_THIRD_PARTY_ACTION = (
    "Handle ticket within official support channels. Ensure no external platforms are referenced."
)


def perform_safety_scan(customer_reply: str, recommended_next_action: str) -> Tuple[bool, List[str], List[str]]:
    """
    Scans customer_reply and recommended_next_action for safety violations.
    Returns:
        is_safe (bool)
        violations (list of string descriptions)
        violated_types (list of types: 'credential', 'refund', 'third_party')
    """
    violations = []
    violated_types = []
    
    # Text to search (both fields combined)
    text_to_scan = f"{customer_reply} | {recommended_next_action}"
    
    # 1. Credentials Check
    for pattern in CREDENTIAL_PATTERNS:
        if re.search(pattern, text_to_scan):
            violations.append("PIN/OTP/password/card-number request pattern detected.")
            violated_types.append("credential")
            break
            
    # 2. Refund Promise Check
    for pattern in REFUND_PROMISE_PATTERNS:
        if re.search(pattern, text_to_scan):
            violations.append("Unauthorized refund promise pattern detected.")
            violated_types.append("refund")
            break
            
    # 3. Third Party Contact Check
    for pattern in THIRD_PARTY_PATTERNS:
        if re.search(pattern, text_to_scan):
            violations.append("Instruction to contact third party outside official channels detected.")
            violated_types.append("third_party")
            break
            
    return len(violations) == 0, violations, violated_types


def apply_safe_templates(
    raw_response: dict,
    violated_types: List[str]
) -> dict:
    """
    Applies fallback templates for the specific violated safety types.
    """
    sanitized = raw_response.copy()
    
    # If multiple, credential has highest priority, then refund, then third party.
    if "credential" in violated_types:
        sanitized["customer_reply"] = SAFE_CREDENTIAL_REPLY
        sanitized["recommended_next_action"] = SAFE_CREDENTIAL_ACTION
        if "reason_codes" not in sanitized or sanitized["reason_codes"] is None:
            sanitized["reason_codes"] = []
        sanitized["reason_codes"].append("safety_credential_violation_remediated")
    elif "refund" in violated_types:
        sanitized["customer_reply"] = SAFE_REFUND_REPLY
        sanitized["recommended_next_action"] = SAFE_REFUND_ACTION
        if "reason_codes" not in sanitized or sanitized["reason_codes"] is None:
            sanitized["reason_codes"] = []
        sanitized["reason_codes"].append("safety_refund_violation_remediated")
    elif "third_party" in violated_types:
        sanitized["customer_reply"] = SAFE_THIRD_PARTY_REPLY
        sanitized["recommended_next_action"] = SAFE_THIRD_PARTY_ACTION
        if "reason_codes" not in sanitized or sanitized["reason_codes"] is None:
            sanitized["reason_codes"] = []
        sanitized["reason_codes"].append("safety_third_party_violation_remediated")
        
    return sanitized


def validate_and_sanitize_response(
    raw_response: dict,
    request_payload: AnalyzeTicketRequest
) -> dict:
    """
    Performs transaction_id grounding and Enum validation safety nets.
    Modifies fields inline to protect against LLM hallucinations or off-list enums.
    """
    sanitized = raw_response.copy()
    
    # 1. Grounding check
    relevant_tx_id = sanitized.get("relevant_transaction_id")
    if relevant_tx_id in ("null", "None", ""):
        sanitized["relevant_transaction_id"] = None
        relevant_tx_id = None
        
    if relevant_tx_id:
        # Check if transaction history is available and matches
        tx_history = request_payload.transaction_history or []
        valid_ids = {tx.transaction_id for tx in tx_history}
        if relevant_tx_id not in valid_ids:
            # Hallucination! Set to null, evidence_verdict to insufficient_data, and add reason code.
            sanitized["relevant_transaction_id"] = None
            sanitized["evidence_verdict"] = EvidenceVerdictEnum.INSUFFICIENT_DATA.value
            if "reason_codes" not in sanitized or sanitized["reason_codes"] is None:
                sanitized["reason_codes"] = []
            sanitized["reason_codes"].append("transaction_id_validation_failed")
            
    # 2. Enum Coercion Safety Net
    
    # Evidence Verdict
    if sanitized.get("evidence_verdict") not in [e.value for e in EvidenceVerdictEnum]:
        sanitized["evidence_verdict"] = EvidenceVerdictEnum.INSUFFICIENT_DATA.value
        
    # Case Type
    if sanitized.get("case_type") not in [e.value for e in CaseTypeEnum]:
        sanitized["case_type"] = CaseTypeEnum.OTHER.value
        
    # Severity
    if sanitized.get("severity") not in [e.value for e in SeverityEnum]:
        # Coerce based on case type if possible, otherwise LOW
        case_type = sanitized.get("case_type")
        if case_type == CaseTypeEnum.PHISHING_OR_SOCIAL_ENGINEERING.value:
            sanitized["severity"] = SeverityEnum.CRITICAL.value
        else:
            sanitized["severity"] = SeverityEnum.LOW.value
            
    # Department
    if sanitized.get("department") not in [e.value for e in DepartmentEnum]:
        # Coerce based on case type
        case_type = sanitized.get("case_type")
        mapping = {
            CaseTypeEnum.PHISHING_OR_SOCIAL_ENGINEERING.value: DepartmentEnum.FRAUD_RISK.value,
            CaseTypeEnum.WRONG_TRANSFER.value: DepartmentEnum.DISPUTE_RESOLUTION.value,
            CaseTypeEnum.PAYMENT_FAILED.value: DepartmentEnum.PAYMENTS_OPS.value,
            CaseTypeEnum.DUPLICATE_PAYMENT.value: DepartmentEnum.PAYMENTS_OPS.value,
            CaseTypeEnum.REFUND_REQUEST.value: DepartmentEnum.CUSTOMER_SUPPORT.value,
            CaseTypeEnum.MERCHANT_SETTLEMENT_DELAY.value: DepartmentEnum.MERCHANT_OPERATIONS.value,
            CaseTypeEnum.AGENT_CASH_IN_ISSUE.value: DepartmentEnum.AGENT_OPERATIONS.value,
        }
        sanitized["department"] = mapping.get(case_type, DepartmentEnum.CUSTOMER_SUPPORT.value).value
        
    # 3. Extra validation logic for phishing_or_social_engineering
    if sanitized.get("case_type") == CaseTypeEnum.PHISHING_OR_SOCIAL_ENGINEERING.value:
        sanitized["severity"] = SeverityEnum.CRITICAL.value
        sanitized["department"] = DepartmentEnum.FRAUD_RISK.value
        sanitized["human_review_required"] = True
    else:
        # Enforce human_review_required guidance
        c_type = sanitized.get("case_type")
        e_verdict = sanitized.get("evidence_verdict")
        
        if e_verdict == EvidenceVerdictEnum.INSUFFICIENT_DATA.value:
            sanitized["human_review_required"] = False
        elif e_verdict == EvidenceVerdictEnum.INCONSISTENT.value:
            sanitized["human_review_required"] = True
        elif c_type in (
            CaseTypeEnum.WRONG_TRANSFER.value,
            CaseTypeEnum.DUPLICATE_PAYMENT.value,
            CaseTypeEnum.AGENT_CASH_IN_ISSUE.value
        ):
            sanitized["human_review_required"] = True
        else:
            sanitized["human_review_required"] = False
            
    return sanitized
