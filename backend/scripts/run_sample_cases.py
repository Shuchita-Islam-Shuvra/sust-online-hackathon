"""Run the public sample case pack against POST /analyze-ticket.

For each case, prints a one-line pass/fail and at the end a summary table
of every checked field. Exits non-zero if any case fails.

Usage (from backend/):
    .venv/bin/python -m scripts.run_sample_cases
or
    .venv/bin/python scripts/run_sample_cases.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000")
CASES_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_cases.json"


# ----- safety check on customer_reply -----

_BAD_PHRASES = [
    re.compile(r"(?i)\bplease\s+(?:share|send|provide|give|tell|confirm|verify|enter|type)\b[^.]{0,40}\b(?:pin|otp|password|cvv|card number)\b"),
    re.compile(r"(?i)\b(?:we (?:will|shall|are going to)|we'll)\s+(?:refund|reverse|return|credit|unblock|recover)\b"),
    re.compile(r"(?i)\byour\s+(?:refund|reversal)\s+(?:has been|is|will be)\s+(?:approved|processed|done|completed|initiated)\b"),
    re.compile(r"(?i)\bcontact\s+(?:the|this)\s+(?:recipient|sender|merchant|number|person)\b"),
    re.compile(r"(?i)\b(?:call|contact|reach|message)\s+\+?88\d{8,}\b"),
]


def safety_check(reply: str) -> list[str]:
    return [p.pattern for p in _BAD_PHRASES if p.search(reply)]


# ----- HTTP -----

def post_analyze(payload: dict[str, Any], timeout: float = 60.0) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + "/analyze-ticket",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


# ----- field comparison -----

def _ok(actual: Any, expected: Any) -> bool:
    if expected is None:
        return actual is None
    return actual == expected


# ----- main -----

def main() -> int:
    if not CASES_PATH.exists():
        print(f"FAIL: cases file not found at {CASES_PATH}")
        return 1

    with CASES_PATH.open("r", encoding="utf-8") as f:
        pack = json.load(f)
    cases = pack.get("cases", [])
    print(f"Loaded {len(cases)} cases from {CASES_PATH.name}")
    print(f"Target: {BASE_URL}")
    print()

    # Health probe first.
    try:
        with urllib.request.urlopen(BASE_URL + "/health", timeout=10) as resp:
            print(f"/health -> {resp.status} {resp.read().decode()}")
    except Exception as exc:
        print(f"FAIL: /health unreachable: {exc}")
        return 1
    print()

    rows: list[dict[str, Any]] = []
    for case in cases:
        cid = case["id"]
        label = case.get("label", "")
        payload = case["input"]
        expected = case["expected_output"]

        t0 = time.time()
        status, body = post_analyze(payload)
        elapsed_ms = int((time.time() - t0) * 1000)

        row: dict[str, Any] = {
            "id": cid,
            "label": label,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "checks": {},
        }

        if status != 200 or not isinstance(body, dict):
            row["checks"]["http"] = False
            row["error"] = body
            rows.append(row)
            print(f"[{cid}] {label}  ->  HTTP {status}  FAIL  ({elapsed_ms} ms)")
            print(f"  body: {body}")
            continue

        # Required-field equality.
        for key in (
            "relevant_transaction_id",
            "evidence_verdict",
            "case_type",
            "department",
        ):
            row["checks"][key] = _ok(body.get(key), expected.get(key))

        # Severity is "comparable" per the problem statement. We require a
        # bucket match (low / medium / high / critical) but allow it to be
        # exactly equal.
        row["checks"]["severity"] = _ok(body.get("severity"), expected.get("severity"))

        # human_review_required: exact match.
        row["checks"]["human_review_required"] = _ok(
            body.get("human_review_required"), expected.get("human_review_required")
        )

        # Safety check on customer_reply.
        reply = body.get("customer_reply", "") or ""
        bad = safety_check(reply)
        row["checks"]["customer_reply_safe"] = not bad
        row["safety_violations"] = bad

        # ticket_id must be echoed.
        row["checks"]["ticket_id_echoed"] = body.get("ticket_id") == payload.get("ticket_id")

        rows.append(row)

        passed = sum(1 for v in row["checks"].values() if v)
        total = len(row["checks"])
        verdict = "PASS" if passed == total else "FAIL"
        print(
            f"[{cid}] {label}  ->  {verdict}  {passed}/{total}  ({elapsed_ms} ms)"
        )
        if verdict == "FAIL":
            for k, v in row["checks"].items():
                if not v:
                    print(f"    - {k}: expected={expected.get(k)!r}  actual={body.get(k)!r}")
            if row.get("safety_violations"):
                print(f"    - safety violations in customer_reply: {row['safety_violations']}")
                print(f"    - reply: {reply!r}")
        print()

    # ---- Summary table ----
    print("=" * 80)
    print(f"SUMMARY  ({len(rows)} cases)")
    print("=" * 80)
    headers = ["id", "case_type", "evidence", "relev_tx", "dept", "sev", "review", "safe", "ok", "ms"]
    print("  ".join(f"{h:<10}" for h in headers))
    print("-" * 80)
    for row in rows:
        c = row["checks"]
        if "error" in row:
            print(f"  ".join([f"{row['id']:<10}", "ERR", "-", "-", "-", "-", "-", "-", "0/8", "-"]))
            continue
        ok_count = sum(1 for v in c.values() if v)
        total = len(c)
        cells = [
            f"{row['id']:<10}",
            f"{c.get('case_type', False)!s:<10}",
            f"{c.get('evidence_verdict', False)!s:<10}",
            f"{c.get('relevant_transaction_id', False)!s:<10}",
            f"{c.get('department', False)!s:<10}",
            f"{c.get('severity', False)!s:<10}",
            f"{c.get('human_review_required', False)!s:<10}",
            f"{c.get('customer_reply_safe', False)!s:<10}",
            f"{ok_count}/{total}".ljust(10),
            f"{row['elapsed_ms']}",
        ]
        print("  ".join(cells))
    print()

    # ---- Exit code ----
    all_ok = all(
        all(c.values()) for r in rows if "checks" in r for c in [r["checks"]]
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())