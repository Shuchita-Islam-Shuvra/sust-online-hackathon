# QueueStorm Investigator — FastAPI Support-Ops Copilot

QueueStorm Investigator is a complete, production-ready FastAPI service designed to automate customer support ticket analysis, transaction history grounding, and secure drafting of customer replies.

## Tech Stack
- **Web Framework**: FastAPI (Python 3.11)
- **Validation**: Pydantic v2 (leveraging Enum classes for structural enforcement)
- **HTTP Client**: `httpx` (async HTTP client with connection pooling and timeouts)
- **Environment**: `python-dotenv` for configuration
- **Containerization**: Docker (slim Python-3.11 base image)

---

## AI & Orchestration Architecture

This service operates on a **zero-rules-engine** philosophy for classification and reasoning. All core decisions—determining case category, assessing severity, checking consistency with transactions, and drafting custom replies—are done by the LLM itself, which is evaluated on genuine reasoning capacity over simple keyword matching.

```
       +---------------------------------------------+
       |           POST /analyze-ticket              |
       +----------------------+----------------------+
                              |
                              v
       +----------------------+----------------------+
       |          Tier 1: Gemini 2.5 Flash           | (Timeout: 30s)
       +----------------------+----------------------+
                              | (If fails/timeouts)
                              v
       +----------------------+----------------------+
       |           Tier 2: Groq Llama 3.1 8B         | (Timeout: 30s)
       +----------------------+----------------------+
                              | (If fails/timeouts)
                              v
       +----------------------+----------------------+
       |           Tier 3: Groq Llama 3.3 70B        | (Timeout: 30s)
       +----------------------+----------------------+
                              | (If all fail)
                              v
       +----------------------+----------------------+
       |        Degraded Fallback response (200)     |
       +---------------------------------------------+
```

### 3-Tier Provider Fallback Chain
To ensure maximum availability, low latency, and resilience to infrastructure/rate limit issues, we implement a 3-tier fallback hierarchy. Each tier gets a **30-second timeout** to ensure complex reasoning does not time out under high latency.

1. **Tier 1 (Gemini - `gemini-2.5-flash`)**: Google's native model using the Generative Language endpoint (`generateContent` with structured `responseSchema`). This serves as the primary tier, offering native JSON schema compliance and advanced reasoning capabilities.
2. **Tier 2 (Groq - `llama-3.1-8b-instant`)**: A lightning-fast model hosted on Groq's high-speed LPU infrastructure, providing rapid response times to cover request volume.
3. **Tier 3 (Groq - `llama-3.3-70b-versatile`)**: A larger Llama model hosted on Groq. This is used if both Tier 1 and Tier 2 fail, serving as a robust fallback.
4. **Degraded Fallback**: If all three tiers fail to return valid structured responses within their time budget, the service returns a degraded-but-valid 200 response indicating automated services are temporarily unavailable, defaulting to `case_type: "other"`, `evidence_verdict: "insufficient_data"`, `human_review_required: true`, and a generic safe customer reply (translated to Bangla if request language is "bn").

---

## Safety & Validation Net (Post-LLM)

After a tier succeeds, it passes through a code-side validation net.

### 1. Grounding Validation (`relevant_transaction_id`)
The LLM response is checked against the request's transaction history. If `relevant_transaction_id` is present but does not match any transaction in the history:
- It is set to `null` (None).
- `evidence_verdict` is set to `"insufficient_data"`.
- `"transaction_id_validation_failed"` is appended to `reason_codes`.

### 2. Enum Coercion Safety Net
If the LLM outputs off-list/invalid string values for enum fields (`case_type`, `department`, `severity`, `evidence_verdict`), the code-side validation will coerce them to safe defaults (e.g. `case_type: "other"`, `department: "customer_support"`, `severity: "low"`) rather than failing validation and returning an HTTP error.

### 3. Safety scan on `customer_reply` and `recommended_next_action`
The system performs a binary regex check against three high-risk compliance failure modes:
1. **Sensitive Credentials Requests**: Catching requests for PIN, OTP, password, CVV, or card number.
2. **Unauthorized Refund Promises**: Catching explicit promises of money returned, "we will refund", or "refund is processed".
3. **Outside Official Support Channels**: Catching references to Telegram, WhatsApp, Viber, or external personal numbers.

**Remediation Loop**:
- If a violation is caught, the service re-asks the *same tier* to rewrite the fields, explicitly flagging the safety violation.
- If the second attempt fails or still violates safety, the service automatically replaces the offending text with a safe, compliant template response and logs `"safety_<type>_violation_remediated"` to `reason_codes`.

---

## Setup & Running Guide

### Environment Configuration
Create a `.env` file in the root directory (based on `.env.example`):
```env
PORT=8000
GROQ_API_KEY=your_groq_api_key
GEMINI_API_KEY=your_gemini_api_key
GROQ_MODEL_TIER1=llama-3.1-8b-instant
GROQ_MODEL_TIER3=llama-3.3-70b-versatile
```

### Run Locally (Python)
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the FastAPI server:
   * **Direct local execution**:
     ```bash
     python -m app.main
     ```
   * **Development execution with hot reload**:
     ```bash
     python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
     ```
3. Test health:
   ```bash
   curl http://localhost:8000/health
   ```

---

## Deployment & Containerization Guide

### Docker Build
1. Build the production Docker image:
   ```bash
   docker build -t queuestorm-investigator .
   ```

### Docker Run (Production)
You can run the container by either supplying an environment file or passing the variables explicitly:

* **Method A: Using an environment file**:
  ```bash
  docker run -d \
    -p 8000:8000 \
    --env-file .env \
    --name queuestorm-service \
    queuestorm-investigator
  ```

* **Method B: Passing variables explicitly**:
  ```bash
  docker run -d \
    -p 8000:8000 \
    -e PORT=8000 \
    -e GROQ_API_KEY="your_groq_api_key" \
    -e GEMINI_API_KEY="your_gemini_api_key" \
    -e GROQ_MODEL_TIER1="llama-3.1-8b-instant" \
    -e GROQ_MODEL_TIER3="llama-3.3-70b-versatile" \
    --name queuestorm-service \
    queuestorm-investigator
  ```

### Verify Container Status
1. Check running logs:
   ```bash
   docker logs -f queuestorm-service
   ```
2. Check container health:
   ```bash
   curl http://localhost:8000/health
   ```

---

## Testing & Integration

### Run Public Sample Cases
To validate the service against the 10 public sample test cases (checks assertions on classification accuracy and formatting):
```bash
python test_cases.py test_cases.json
```

### Run Adversarial Stress Cases
To validate the service against the 10 adversarial/stress test cases (checks structural safety, prompt-injection immunity, and compliance enums):
```bash
python more_test.py
```

---

## Models Selection & Cost Reasoning
- **Gemini Tier 1 Model (`gemini-2.5-flash`)**: High capability, supports structured JSON schema out-of-the-box, has a generous rate limit and low cost per million tokens.
- **Groq Tier 2 Model (`llama-3.1-8b-instant`)**: Exceptionally low latency, providing rapid fallback times to cover request volume.
- **Groq Tier 3 Model (`llama-3.3-70b-versatile`)**: Offers near-frontier model reasoning capability while still running on ultra-fast Groq hardware, serving as a perfect high-reasoning fallback.

## Assumptions
- The transaction history, if provided, contains a valid `transaction_id` for grounding matching.
- The `language` field, if provided, follows `en`, `bn`, or `mixed` values.
- All timestamps in the transaction history are ISO 8601 formatted strings.

## Known Limitations
- Network latency overhead of the model provider can occasionally delay p95 times if Tier 1 is down and we fall back to Tier 2. However, the 30s timeout keeps the absolute maximum wait well under 90s.
- Custom regexes check for safety compliance patterns. Highly creative ways of asking for credentials or promising refunds may slip through if not caught by the LLM system prompt; however, the LLM itself is strongly prompted to avoid this.
