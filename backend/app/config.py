"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env from the backend/ directory (one level above app/).
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    groq_api_key: str | None
    groq_model: str
    request_timeout_seconds: float
    cors_origins: list[str]


def _parse_cors(raw: str | None) -> list[str]:
    if not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def get_settings() -> Settings:
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        groq_api_key=os.getenv("GROQ_API_KEY"),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "25")),
        cors_origins=_parse_cors(os.getenv("CORS_ORIGINS")),
    )