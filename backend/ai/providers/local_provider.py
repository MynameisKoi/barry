"""Local LLM / NVIDIA DGX Spark Provider for BARRY Intelligence Engine.

Connects to OpenAI-compatible local inference servers (vLLM, Ollama, llama.cpp,
or TensorRT-LLM on NVIDIA DGX Spark) using the unified LLMProvider interface.
"""

from backend.ai.providers.openrouter_provider import OpenRouterProvider


class LocalLLMProvider(OpenRouterProvider):
    """Adapter for local inference servers (e.g., NVIDIA DGX Spark)."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "local-dgx",
        model_name: str = "llama3.1:8b",
        timeout: float = 30.0,
        max_retries: int = 1,
    ) -> None:
        super().__init__(
            api_key=api_key or "local-dgx",
            model_name=model_name,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            provider_label="local",
        )
