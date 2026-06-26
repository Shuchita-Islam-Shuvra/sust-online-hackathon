"""Pydantic request/response models for the QueueStorm Investigator API.

These types intentionally mirror the schemas in the problem statement so the
judge harness can validate responses directly. Enum values are exact.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------- Enums (Section 7) ----------

class CaseType(str, Enum):
    wrong_transfer = "wrong_transfer"
    payment_failed = "payment_failed"
    refund_request = "refund_request"
    duplicate_payment = "duplicate_payment"
    merchant_settlement_delay = "merchant_settlement_delay"
    agent_cash_in_issue = "agent_cash_in_issue"
    phishing_or_social_engineering = "phishing_or_social_engineering"
    other = "other"


class Department(str, Enum):
    customer_support = "customer_support"
    dispute_resolution = "dispute_resolution"
    payments_ops = "payments_ops"
    merchant_operations = "merchant_operations"
    agent_operations = "agent_operations"
    fraud_risk = "fraud_risk"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class EvidenceVerdict(str, Enum):
    consistent = "consistent"
    inconsistent = "inconsistent"
    insufficient_data = "insufficient_data"


class TransactionStatus(str, Enum):
    completed = "completed"
    failed = "failed"
    pending = "pending"
    reversed = "reversed"


class TransactionType(str, Enum):
    transfer = "transfer"
    payment = "payment"
    cash_in = "cash_in"
    cash_out = "cash_out"
    settlement = "settlement"
    refund = "refund"


# ---------- Request models (Section 5) ----------

class TransactionHistoryEntry(BaseModel):
    transaction_id: str
    timestamp: str
    type: TransactionType
    amount: float
    counterparty: str
    status: TransactionStatus


class AnalyzeRequest(BaseModel):
    ticket_id: str = Field(..., min_length=1)
    complaint: str = Field(..., min_length=1, max_length=8000)
    language: Optional[Literal["en", "bn", "mixed"]] = None
    channel: Optional[str] = None
    user_type: Optional[str] = None
    campaign_context: Optional[str] = None
    transaction_history: list[TransactionHistoryEntry] = Field(default_factory=list)
    metadata: Optional[dict] = None

    @field_validator("complaint")
    @classmethod
    def _strip_complaint(cls, v: str) -> str:
        # The model handles safety; we just trim whitespace.
        return v.strip()


# ---------- Response models (Section 6) ----------

class AnalyzeResponse(BaseModel):
    ticket_id: str
    relevant_transaction_id: Optional[str] = None
    evidence_verdict: EvidenceVerdict
    case_type: CaseType
    severity: Severity
    department: Department
    agent_summary: str = Field(..., min_length=1)
    recommended_next_action: str = Field(..., min_length=1)
    customer_reply: str = Field(..., min_length=1)
    human_review_required: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None