# QueueStorm Investigator — Runbook

Backend service for the **QueueStorm Investigator** AI/API SupportOps challenge.
Exposes two HTTP endpoints:

- `GET  /health`  → `{"status":"ok"}` (must respond within 60s of startup)
- `POST /analyze-ticket` → structured JSON analysis (must respond within 30s)

---

## 1. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Web framework | FastAPI 0.138 | Async, OpenAPI, Pydantic v2 native |
| LLM | Groq (`llama-3.3-70b-versatile`) | Free tier, fast, supports `response_format=json_object` |
| Validation | Pydantic v2 | Strict enums matching the problem statement exactly |
| Safety | Regex + phrase rewriting (no LLM) | Deterministic, no chance of regression by the model |
| Server | Uvicorn | Standard ASGI server |

No database, no auth, no frontend — by design. The service is stateless.

---

## 2. Setup (local)

```bash
cd backend
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .

cp .env.example .env
# Edit .env and set GROQ_API_KEY
```

The Groq SDK and `python-dotenv` are pulled in by `pyproject.toml`.

## 3. Run

```bash
.venv/bin/python main.py
# or
.venv/bin/uvicorn app.app:app --host 0.0.0.0 --port 8000
```

You should see:

```
QueueStorm Investigator ready (model=llama-3.3-70b-versatile)
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## 4. Smoke test

With the server running:

```bash
.venv/bin/python -m scripts.smoke_test
```

This hits `/health` and `POST /analyze-ticket` with a synthetic wrong-transfer
case, then asserts that the response has every required field and that the
`customer_reply` does not contain unsafe phrases.

## 5. API contract

### GET /health

```json
{ "status": "ok" }
```

### POST /analyze-ticket

Request body: see the problem statement, Section 5. The service expects a
JSON object with at minimum `ticket_id` and `complaint`.

Response body (Section 6) — every field is required:

```json
{
  "ticket_id": "TKT-001",
  "relevant_transaction_id": "TXN-9101",
  "evidence_verdict": "consistent",
  "case_type": "wrong_transfer",
  "severity": "high",
  "department": "dispute_resolution",
  "agent_summary": "...",
  "recommended_next_action": "...",
  "customer_reply": "...",
  "human_review_required": true,
  "confidence": 0.9,
  "reason_codes": ["wrong_transfer", "transaction_match"]
}
```

HTTP status codes:
- `200` success
- `400` malformed input (invalid JSON / missing required field)
- `422` schema-valid but semantically invalid (e.g. empty complaint)
- `500` internal error (e.g. LLM provider unreachable). The body is
  `{"error": "internal_error"}` — never a stack trace.

## 6. Safety logic

The system prompt forbids the LLM from asking for credentials, confirming
refunds, or directing to suspicious third parties. As a second line of
defense, the response is post-processed by `app/safety.py`:

- Credential requests (`"share your PIN"`, `"send the OTP"`, …) are
  replaced with the standard security reminder.
- Unauthorized commitments (`"we will refund you"`, `"your refund has been
  approved"`, …) are replaced with safe hedging.
- Third-party contact instructions (`"contact the recipient at +8801..."`)
  are replaced with a redirect to official channels.
- Customer complaint text is fenced with `<<<CUSTOMER_COMPLAINT_BEGIN>>>`
  markers and stripped of obvious prompt-injection phrases before being
  sent to the model, so the LLM cannot be hijacked by the user.

Any field that was rewritten gets a `safety_rewrite:<rule>` entry appended
to `reason_codes` so reviewers can see what fired.

## 7. Models

- `llama-3.3-70b-versatile` (Groq) — chosen for:
  - strong JSON adherence with `response_format=json_object`
  - low latency (well under the 30s budget on free tier)
  - no GPU required, no large model download

No model is baked into the Docker image; the API key is the only secret.

## 8. Environment variables

| Var | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | yes | — | Groq API key |
| `GROQ_MODEL` | no | `llama-3.3-70b-versatile` | Model id |
| `REQUEST_TIMEOUT_SECONDS` | no | `25` | Per-request LLM timeout |
| `CORS_ORIGINS` | no | `*` | Comma-separated allowed origins |

## 9. Docker (optional)

```bash
docker build -t queuestorm-investigator .
docker run --rm -p 8000:8000 -e GROQ_API_KEY=$GROQ_API_KEY queuestorm-investigator
```

`Dockerfile` is included.

## 10. Known limitations

- Single LLM provider. If Groq is down, `/analyze-ticket` returns 500.
- Safety rewrites are regex-based. They cover the most common phrasings
  seen in the problem statement but can be bypassed by highly creative
  rewordings. The system prompt is the primary defense.
- No persistence. Each request is processed in isolation. This is
  intentional for a stateless copilot and matches the problem statement.