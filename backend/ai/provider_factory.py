"""Provider Factory for BARRY Intelligence Engine.

Selects and instantiates the configured LLMProvider implementation at runtime
without coupling application logic to any specific AI vendor.
"""

import logging
from typing import Optional

from backend.ai.interface import LLMProvider
from backend.ai.providers.anthropic_provider import AnthropicProvider
from backend.ai.providers.google_provider import GoogleProvider
from backend.ai.providers.local_provider import LocalLLMProvider
from backend.ai.providers.mock_provider import MockProvider
from backend.ai.providers.openai_provider import OpenAIProvider
from backend.ai.providers.openrouter_provider import OpenRouterProvider
from backend.config import Settings, get_settings

logger = logging.getLogger("barry.ai.factory")


def create_provider_by_name(
    provider_name: str,
    settings: Optional[Settings] = None,
) -> LLMProvider:
    """Instantiate a specific LLMProvider implementation by name."""
    cfg = settings or get_settings()
    name = (provider_name or "mock").strip().lower()

    if name == "mock":
        return MockProvider(model_name="barry-mock-v1")
    if name == "openrouter":
        return OpenRouterProvider(
            api_key=cfg.OPENROUTER_API_KEY,
            model_name=cfg.LLM_MODEL,
            base_url=cfg.OPENROUTER_BASE_URL,
            timeout=cfg.AI_TIMEOUT,
            max_retries=cfg.AI_MAX_RETRIES,
        )
    if name == "openai":
        return OpenAIProvider(
            api_key=cfg.OPENAI_API_KEY,
            model_name=cfg.OPENAI_MODEL,
            timeout=cfg.AI_TIMEOUT,
            max_retries=cfg.AI_MAX_RETRIES,
        )
    if name == "anthropic":
        return AnthropicProvider(
            api_key=cfg.ANTHROPIC_API_KEY,
            model_name=cfg.ANTHROPIC_MODEL,
            timeout=cfg.AI_TIMEOUT,
        )
    if name == "google":
        return GoogleProvider(
            api_key=cfg.GOOGLE_API_KEY,
            model_name=cfg.GOOGLE_MODEL,
            timeout=cfg.AI_TIMEOUT,
        )
    if name in ("local", "dgx", "dgx_spark"):
        return LocalLLMProvider(
            base_url=cfg.LOCAL_LLM_BASE_URL,
            api_key=cfg.LOCAL_LLM_API_KEY,
            model_name=cfg.LOCAL_LLM_MODEL,
            timeout=cfg.AI_TIMEOUT,
            max_retries=cfg.AI_MAX_RETRIES,
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider_name}'. "
        "Supported values: mock, openrouter, openai, anthropic, google, local"
    )


def get_llm_provider(settings: Optional[Settings] = None) -> LLMProvider:
    """Return the configured primary LLMProvider instance."""
    cfg = settings or get_settings()
    return create_provider_by_name(cfg.LLM_PROVIDER, cfg)


def get_fallback_provider(settings: Optional[Settings] = None) -> Optional[LLMProvider]:
    """Return the configured fallback LLMProvider instance, if any."""
    cfg = settings or get_settings()
    fallback_name = (cfg.FALLBACK_LLM_PROVIDER or "").strip().lower()
    if not fallback_name:
        return None
    try:
        return create_provider_by_name(fallback_name, cfg)
    except ValueError as exc:
        logger.warning("Invalid fallback provider '%s': %s", fallback_name, exc)
        return None
