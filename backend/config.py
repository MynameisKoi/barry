"""Centralized Configuration for BARRY Backend & Intelligence Engine."""

import os
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    """Application settings loaded from environment variables with optional keyword overrides."""

    def __init__(self, **overrides: Any) -> None:
        self.HOST: str = os.getenv("HOST", "0.0.0.0")
        self.PORT: int = int(os.getenv("PORT", "8000"))
        self.ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")

        # Database
        self.DATABASE_PATH: str = os.getenv(
            "DATABASE_PATH", str(BASE_DIR / "barry.db")
        )

        # Robot Mode
        self.ROBOT_MODE: str = os.getenv("ROBOT_MODE", "simulation").lower()

        # LLM Provider Configuration
        # Defaults to 'openrouter' if OPENROUTER_API_KEY is present and LLM_PROVIDER not explicitly set,
        # otherwise defaults to 'mock' so the app starts cleanly without API keys.
        raw_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        self.OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not raw_provider:
            self.LLM_PROVIDER: str = "openrouter" if self.OPENROUTER_API_KEY else "mock"
        else:
            self.LLM_PROVIDER = raw_provider

        self.LLM_MODEL: str = os.getenv(
            "LLM_MODEL", "openai/gpt-4o-mini"
        ).strip()
        self.FALLBACK_LLM_PROVIDER: str = os.getenv(
            "FALLBACK_LLM_PROVIDER", "mock"
        ).strip().lower()

        # OpenRouter
        self.OPENROUTER_BASE_URL: str = os.getenv(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ).rstrip("/")

        # Direct Provider Credentials (Future / Optional)
        self.OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
        self.OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

        self.ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.ANTHROPIC_MODEL: str = os.getenv(
            "ANTHROPIC_MODEL", "claude-3-5-haiku-latest"
        ).strip()

        self.GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "").strip()
        self.GOOGLE_MODEL: str = os.getenv("GOOGLE_MODEL", "gemini-2.0-flash").strip()

        # Local LLM / NVIDIA DGX Spark Configuration
        self.LOCAL_LLM_BASE_URL: str = os.getenv(
            "LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"
        ).rstrip("/")
        self.LOCAL_LLM_API_KEY: str = os.getenv("LOCAL_LLM_API_KEY", "local-dgx").strip()
        self.LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "llama3.1:8b").strip()

        # Timeouts & Retries
        self.AI_TIMEOUT: float = float(os.getenv("AI_TIMEOUT", "30"))
        self.AI_MAX_RETRIES: int = int(os.getenv("AI_MAX_RETRIES", "2"))

        for key, value in overrides.items():
            setattr(self, key.upper(), value)


def get_settings() -> Settings:
    """Returns fresh Settings instance reflecting current environment."""
    return Settings()


settings = get_settings()
