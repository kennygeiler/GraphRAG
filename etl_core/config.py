"""Environment bootstrap: LangSmith tracing + Anthropic keys."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_LANGSMITH_VARS = (
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_ENDPOINT",
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_PROJECT",
)


def load_env(dotenv_path: str | Path | None = None) -> None:
    """Load .env once; safe to call multiple times."""
    load_dotenv(dotenv_path or Path.cwd() / ".env")


def enable_langsmith() -> bool:
    """
    Ensure LangSmith env vars are set from .env before any LangChain import.

    Returns True if tracing is configured (key present + tracing enabled).
    """
    load_env()
    tracing = os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in ("true", "1", "yes")
    has_key = bool(os.environ.get("LANGCHAIN_API_KEY"))
    if tracing and has_key:
        os.environ.setdefault("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
        os.environ.setdefault("LANGCHAIN_PROJECT", "narrative-mri")
    return tracing and has_key


def require_anthropic_key() -> str:
    load_env()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set in environment or .env")
    return key
