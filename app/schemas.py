from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

# --- Enums ---

class LanguageEnum(str, Enum):
    EN = "en"
    BN = "bn"
    MIXED = "mixed"

class ChannelEnum(str, Enum):
    IN_APP_CHAT = "in_app_chat"
    CALL_CENTER = "call_center"
    EMAIL = "email"
    MERCHANT_PORTAL = "merchant_portal"
    FIELD_AGENT = "field_agent"

class UserTypeEnum(str, Enum):
    CUSTOMER = "customer"
    MERCHANT = "merchant"
    AGENT = "agent"
    UNKNOWN = "unknown"

class TransactionTypeEnum(str, Enum):
    TRANSFER = "transfer"
    PAYMENT = "payment"
    CASH_IN = "cash_in"
    CASH_OUT = "cash_out"
    SETTLEMENT = "settlement"
    REFUND = "refund"

class TransactionStatusEnum(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    PENDING = "pending"
    REVERSED = "reversed"

class EvidenceVerdictEnum(str, Enum):
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    INSUFFICIENT_DATA = "insufficient_data"

class CaseTypeEnum(str, Enum):
    WRONG_TRANSFER = "wrong_transfer"
    PAYMENT_FAILED = "payment_failed"
    REFUND_REQUEST = "refund_request"
    DUPLICATE_PAYMENT = "duplicate_payment"
    MERCHANT_SETTLEMENT_DELAY = "merchant_settlement_delay"
    AGENT_CASH_IN_ISSUE = "agent_cash_in_issue"
    PHISHING_OR_SOCIAL_ENGINEERING = "phishing_or_social_engineering"
    OTHER = "other"

class SeverityEnum(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class DepartmentEnum(str, Enum):
    CUSTOMER_SUPPORT = "customer_support"
    DISPUTE_RESOLUTION = "dispute_resolution"
    PAYMENTS_OPS = "payments_ops"
    MERCHANT_OPERATIONS = "merchant_operations"
    AGENT_OPERATIONS = "agent_operations"
    FRAUD_RISK = "fraud_risk"


# --- Schemas ---

class TransactionHistoryItem(BaseModel):
    transaction_id: str
    timestamp: str
    type: TransactionTypeEnum
    amount: float
    counterparty: str
    status: TransactionStatusEnum

class AnalyzeTicketRequest(BaseModel):
    ticket_id: str
    complaint: str
    language: Optional[LanguageEnum] = None
    channel: Optional[ChannelEnum] = None
    user_type: Optional[UserTypeEnum] = None
    campaign_context: Optional[str] = None
    transaction_history: Optional[List[TransactionHistoryItem]] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("complaint")
    @classmethod
    def validate_complaint_not_empty(cls, v: str) -> str:
        if not v or v.strip() == "":
            raise ValueError("complaint must not be empty or whitespace only")
        return v

class AnalyzeTicketResponse(BaseModel):
    ticket_id: str
    relevant_transaction_id: Optional[str] = None
    evidence_verdict: EvidenceVerdictEnum
    case_type: CaseTypeEnum
    severity: SeverityEnum
    department: DepartmentEnum
    agent_summary: str
    recommended_next_action: str
    customer_reply: str
    human_review_required: bool
    confidence: Optional[float] = None
    reason_codes: Optional[List[str]] = Field(default_factory=list)
