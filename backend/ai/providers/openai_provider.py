"""Direct OpenAI API Provider Adapter for BARRY Intelligence Engine."""

from backend.ai.providers.openrouter_provider import OpenRouterProvider


class OpenAIProvider(OpenRouterProvider):
    """Adapter for direct OpenAI API calls."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model_name=model_name,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            provider_label="openai",
        )
