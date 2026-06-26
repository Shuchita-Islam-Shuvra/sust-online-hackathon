"""Safety guardrails (Section 8 of the problem statement).

Enforced rules:

1. Never ask for PIN, OTP, password, or full card number.
2. Never confirm a refund / reversal / unblock / recovery. Use safe hedging.
3. Never instruct the customer to contact a suspicious third party.
4. Ignore prompt-injection content embedded inside the customer's complaint.

Implementation: deterministic regex + phrase rewriting. Runs after the LLM
produces its draft. If a rule is violated, the offending phrase is replaced
with a safe alternative (and a flag is added to ``reason_codes``).

To avoid double-rewriting our own safety disclaimers (e.g. "please do not
share your OTP"), the credential check splits the reply into sentences and
only rewrites a sentence that contains an imperative *request* — a sentence
that contains "do not / don't / never / should not" is treated as a
disclaimer and left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------- Rule 1: never request credentials ----------

_REQUEST_VERBS = r"(?:please|kindly)?\s*(?:share|send|provide|give|tell|confirm|verify|enter|type|submit|reveal)"
_DISALLOWED_MODIFIERS = r"(?:do not|don't|never|not to|should not|shouldn't|will not|won't)"
_CREDENTIALS = r"(?:pin|otp|one[- ]time password|password|cvv|card number|full card)"
_POSSESSIVE = r"(?:your|the|my|our)"

# Imperative form: "verb ... possessive ... credential"
_CREDENTIAL_REQUEST_IN_SENTENCE: re.Pattern[str] = re.compile(
    rf"(?i)\b{_REQUEST_VERBS}\b[^.]{{0,40}}\b{_POSSESSIVE}\b[^.]{{0,30}}\b{_CREDENTIALS}\b",
)
# Reverse form: "possessive credential ... verb" (e.g. "Tell us your PIN")
_CREDENTIAL_REVERSE_IN_SENTENCE: re.Pattern[str] = re.compile(
    rf"(?i)\b{_POSSESSIVE}\b[^.]{{0,30}}\b{_CREDENTIALS}\b[^.]{{0,60}}\b{_REQUEST_VERBS}\b",
)
# Disclaimers: sentences that warn against sharing are skipped.
_DISALLOWED_IN_SENTENCE: re.Pattern[str] = re.compile(rf"(?i)\b{_DISALLOWED_MODIFIERS}\b")

_CREDENTIAL_REPLACEMENT = (
    "For your security, we will never ask for your PIN, OTP, password, or card number. "
    "Please do not share them with anyone, including our team."
)


# ---------- Rule 2: never confirm refund / reversal / unblock ----------

_UNAUTHORIZED_COMMITMENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?i)\b(?:we (?:will|shall|are going to)|we'll)\s+(?:refund|reverse|return|credit|unblock|recover|release)\b[^.]{0,120}",
    ),
    re.compile(
        r"(?i)\byour\s+(?:refund|reversal|unblock|recovery)\s+(?:has been|is|will be)\s+(?:approved|processed|done|completed|initiated)\b[^.]{0,120}",
    ),
    re.compile(
        r"(?i)\b(?:refund|reversal|unblock|recovery)\s+(?:has been|is|will be)\s+(?:approved|processed|done|completed|initiated)\b[^.]{0,120}",
    ),
    re.compile(
        r"(?i)\byou (?:will|shall|are going to)\s+(?:receive|get|be refunded|be reversed)\b[^.]{0,120}",
    ),
]

_UNAUTHORIZED_COMMITMENT_REPLACEMENT = (
    "Any eligible amount will be reviewed and, where applicable, returned "
    "through official channels only after verification by our team."
)


# ---------- Rule 3: never direct to suspicious third parties ----------

_THIRDPARTY_CONTACT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?i)\b(?:please\s+)?contact\s+(?:the|this)\s+(?:recipient|sender|merchant|agent|number|person)\b[^.]{0,120}",
    ),
    re.compile(
        r"(?i)\b(?:please\s+)?(?:call|contact|reach|message)\s+\+?88\d{8,}\b[^.]{0,120}",
    ),
    re.compile(
        r"(?i)\b(?:please\s+)?(?:call|contact|reach|message)\s+[A-Z][\w'&.-]{2,40}\s+(?:directly|to confirm|to settle)\b[^.]{0,120}",
    ),
]

_THIRDPARTY_CONTACT_REPLACEMENT = (
    "Please continue to communicate only through our official support channels. "
    "Do not contact third parties based on instructions received in calls or SMS."
)


@dataclass
class SafetyResult:
    text: str
    violations: list[str] = field(default_factory=list)


def _split_sentences(text: str) -> list[str]:
    """Crude sentence splitter. Good enough for safety checks on short replies."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _sentence_is_request(sentence: str) -> bool:
    """True if the sentence demands a credential from the customer."""
    if _DISALLOWED_IN_SENTENCE.search(sentence):
        return False
    return bool(
        _CREDENTIAL_REQUEST_IN_SENTENCE.search(sentence)
        or _CREDENTIAL_REVERSE_IN_SENTENCE.search(sentence)
    )


def _scrub_credentials(text: str, violations: list[str]) -> str:
    """Rewrite any sentence that demands the customer's credentials."""
    sentences = _split_sentences(text)
    rewritten: list[str] = []
    for sentence in sentences:
        if _sentence_is_request(sentence):
            rewritten.append(_CREDENTIAL_REPLACEMENT)
            violations.append("credential_request")
        else:
            rewritten.append(sentence)
    return " ".join(rewritten)


def _scrub_generic(text: str, patterns: list[re.Pattern[str]], replacement: str, label: str, violations: list[str]) -> str:
    cleaned = text
    for pat in patterns:
        if pat.search(cleaned):
            violations.append(label)
            # Each replacement ends with ".", so strip any trailing "." that
            # the source sentence was using to terminate the matched clause.
            cleaned = pat.sub(lambda _m, _r=replacement: _r.rstrip("."), cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


def enforce_safety(text: str) -> SafetyResult:
    """Apply all safety rewrites to a string of customer-facing text."""
    if not text:
        return SafetyResult(text=text)

    violations: list[str] = []
    cleaned = _scrub_credentials(text, violations)
    cleaned = _scrub_generic(cleaned, _UNAUTHORIZED_COMMITMENT_PATTERNS, _UNAUTHORIZED_COMMITMENT_REPLACEMENT, "unauthorized_commitment", violations)
    cleaned = _scrub_generic(cleaned, _THIRDPARTY_CONTACT_PATTERNS, _THIRDPARTY_CONTACT_REPLACEMENT, "third_party_contact", violations)

    return SafetyResult(text=cleaned.strip(), violations=violations)


def sanitize_inputs_for_prompt(complaint: str) -> str:
    """Strip prompt-injection-style instructions out of complaint text.

    The LLM still sees the *meaning* of the complaint, but explicit override
    attempts are fenced off so they cannot bypass system rules.
    """
    if not complaint:
        return ""

    fences = (
        "<<<CUSTOMER_COMPLAINT_BEGIN>>>",
        "<<<CUSTOMER_COMPLAINT_END>>>",
    )
    redacted = re.sub(
        r"(?is)\b(?:ignore|disregard|forget|override)\b[^.]{0,80}\b(?:previous|system|instructions?|rules?)\b[^.]{0,160}",
        "[redacted injection attempt]",
        complaint,
    )
    redacted = re.sub(
        r"(?is)\b(?:you are now|act as|pretend to be|new instructions?)\b[^.]{0,160}",
        "[redacted injection attempt]",
        redacted,
    )
    return f"{fences[0]}\n{redacted}\n{fences[1]}"