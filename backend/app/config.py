"""Centralized configuration for the AgenticFlow Orchestrator backend.

Loads all environment variables via python-dotenv and exposes them
through a validated Pydantic settings object for type-safe access.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# ---------------------------------------------------------------------------
# Load .env from the project root (two levels up from this file)
# ---------------------------------------------------------------------------
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if not _ENV_PATH.exists():
    # Fallback: try the workspace root (three levels up, for monorepo setups)
    _ENV_PATH = Path(__file__).resolve().parents[3] / ".env"

load_dotenv(dotenv_path=_ENV_PATH, override=False)


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All values have sensible defaults so the app can start in dev mode
    without a fully populated .env file.
    """

    # ── Google / Gemini ──────────────────────────────────────────────────
    GOOGLE_API_KEY: str = ""

    # ── Nvidia NIM (fallback LLM) ────────────────────────────────────────
    NVIDIA_FALLBACK_MODEL: str = "meta/llama-3.1-70b-instruct"

    # ── LangSmith (optional tracing) ─────────────────────────────────────
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "autonomous-ai-orchestrator"

    # ── LLM defaults ─────────────────────────────────────────────────────
    PRIMARY_LLM_MODEL: str = "gemini-2.5-flash"
    # Bare model id — LangChain adds a single ``models/`` prefix (avoid models/models/…)
    EMBEDDING_MODEL: str = "gemini-embedding-001"

    # ── RAG / PGVector ───────────────────────────────────────────────────
    FAISS_CHUNK_SIZE: int = 1000
    FAISS_CHUNK_OVERLAP: int = 200

    # ── Graph Database (Memgraph) ─────────────────────────────────────────
    MEMGRAPH_URI: str = "bolt://localhost:7687"

    # ── PostgreSQL persistence ───────────────────────────────────────────
    POSTGRES_URI: str = "postgresql://postgres:password@localhost:5432/agenticflow"

    # ── SQLite persistence (Deprecated) ──────────────────────────────────
    SQLITE_DB_PATH: str = "memory.db"

    # ── CORS ─────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "*"  # Comma-separated in production

    # ── Server ───────────────────────────────────────────────────────────
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8005
    DEBUG: bool = False

    # ── Web Search / Other APIs ──────────────────────────────────────────
    TAVILY_API_KEY: str = ""
    NVIDIA_API_KEY: str = ""

    # ── Security ─────────────────────────────────────────────────────────
    JWT_SECRET: str = "super-secret-key-12345"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """Return a cached singleton of application settings."""
    return Settings()
