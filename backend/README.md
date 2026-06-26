# QueueStorm Investigator — Backend

AI/API SupportOps copilot for digital finance complaints. Exposes two HTTP
endpoints and uses Groq (`llama-3.3-70b-versatile`) for reasoning.

> Full setup, run, and safety notes live in **[RUNBOOK.md](./RUNBOOK.md)**.
> This README gives a one-page summary plus the required **MODELS** section.

## Quick start

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # set GROQ_API_KEY
python main.py              # serves on :8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

Analyze a ticket:

```bash
curl -X POST http://127.0.0.1:8000/analyze-ticket \
  -H "Content-Type: application/json" \
  -d @data/sample_request.json
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health`         | readiness probe (`{"status":"ok"}`) |
| POST | `/analyze-ticket` | structured complaint analysis |

## MODELS

| Model | Where it runs | Why it was chosen |
|---|---|---|
| `llama-3.3-70b-versatile` (via Groq) | Groq cloud, called over HTTPS | Free tier available, sub-second latency, native `response_format=json_object` support, strong adherence to strict system prompts. Avoids baking a multi-GB model into the Docker image, which keeps us under the 5 GB image guidance and lets judges pull only the API key, not model weights. |

No model is downloaded at build time. The service is fully stateless.

## Sample output

A recorded response from `scripts/smoke_test.py` against the public sample
case in the runbook can be reproduced any time by running the smoke test
with `GROQ_API_KEY` set.

## Layout

```
backend/
├── app/
│   ├── app.py             # FastAPI entry, /health, lifespan, CORS
│   ├── config.py          # env loader
│   ├── models.py          # Pydantic enums + request/response schemas
│   ├── prompts.py         # strict system prompt + user-prompt builder
│   ├── safety.py          # deterministic safety scrubber
│   ├── router/
│   │   └── analysis.py    # POST /analyze-ticket
│   └── services/
│       ├── llm.py         # Groq client + JSON extraction
│       └── validate.py    # raw LLM JSON -> typed response
├── scripts/smoke_test.py  # end-to-end test
├── main.py                # uvicorn entry
├── Dockerfile
├── requirements.txt
├── pyproject.toml
├── .env.example
├── .gitignore
├── .dockerignore
└── RUNBOOK.md             # full runbook
```

## License

MIT (or whatever your team uses — set this before submitting).