"""FastAPI application entry point for QueueStorm Investigator."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .models import HealthResponse
from .router.analysis import router as analysis_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("queuestorm")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    if settings.gemini_api_key:
        log.info("QueueStorm Investigator ready (primary: gemini/%s)", settings.gemini_model)
    elif settings.groq_api_key:
        log.info("QueueStorm Investigator ready (primary: groq/%s)", settings.groq_model)
    else:
        log.warning("No LLM keys configured; /analyze-ticket will use rule-based fallback only.")
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="QueueStorm Investigator",
        description="AI/API SupportOps copilot for digital finance complaints.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    # /health must respond immediately; no LLM or DB calls here.
    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    app.include_router(analysis_router)

    @app.exception_handler(Exception)
    async def _safe_error(_: object, exc: Exception) -> JSONResponse:  # type: ignore[override]
        # Never expose stack traces or secrets in error responses.
        log.exception("Unhandled error: %s", exc)
        return JSONResponse(status_code=500, content={"error": "internal_error"})

    return app


app = create_app()