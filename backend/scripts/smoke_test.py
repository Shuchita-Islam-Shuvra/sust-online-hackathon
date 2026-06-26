"""Tiny smoke test for the QueueStorm Investigator API.

Hits /health and POSTs one sample case to /analyze-ticket.

Usage (from backend/):
    .venv/bin/python -m scripts.smoke_test
or
    .venv/bin/python scripts/smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error


BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000")


SAMPLE_REQUEST = {
    "ticket_id": "TKT-SMOKE-001",
    "complaint": (
        "I tried to send 5000 taka to my cousin at +8801711112222 around 2pm "
        "today through the app, but the money was sent to a different number. "
        "Please check transaction TXN-9101 and help me get it back."
    ),
    "language": "en",
    "channel": "in_app_chat",
    "user_type": "customer",
    "campaign_context": "boishakh_bonanza_day_1",
    "transaction_history": [
        {
            "transaction_id": "TXN-9101",
            "timestamp": "2026-04-14T14:08:22Z",
            "type": "transfer",
            "amount": 5000,
            "counterparty": "+8801719876543",
            "status": "completed",
        },
        {
            "transaction_id": "TXN-9100",
            "timestamp": "2026-04-14T13:50:11Z",
            "type": "payment",
            "amount": 250,
            "counterparty": "merchant_8821",
            "status": "completed",
        },
    ],
}


def _request(path: str, payload: dict | None = None) -> tuple[int, dict | str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def main() -> int:
    print(f"[smoke] GET {BASE_URL}/health")
    status, body = _request("/health")
    print(f"[smoke]   -> {status} {body}")
    if status != 200 or (isinstance(body, dict) and body.get("status") != "ok"):
        print("[smoke] FAIL: /health did not return ok")
        return 1

    print(f"[smoke] POST {BASE_URL}/analyze-ticket")
    status, body = _request("/analyze-ticket", SAMPLE_REQUEST)
    print(f"[smoke]   -> {status}")
    print(json.dumps(body, indent=2, ensure_ascii=False))

    if status != 200:
        print("[smoke] FAIL: /analyze-ticket did not return 200")
        return 1

    if not isinstance(body, dict):
        print("[smoke] FAIL: response is not a JSON object")
        return 1

    required = {
        "ticket_id", "relevant_transaction_id", "evidence_verdict",
        "case_type", "severity", "department", "agent_summary",
        "recommended_next_action", "customer_reply", "human_review_required",
        "confidence", "reason_codes",
    }
    missing = required - body.keys()
    if missing:
        print(f"[smoke] FAIL: missing fields: {sorted(missing)}")
        return 1

    # Sanity-check safety on the reply.
    reply = (body.get("customer_reply") or "").lower()
    bad = ["share your pin", "send your otp", "we will refund you", "we will reverse"]
    for phrase in bad:
        if phrase in reply:
            print(f"[smoke] FAIL: customer_reply contains unsafe phrase: {phrase!r}")
            return 1

    print("[smoke] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())