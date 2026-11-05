"""Direct Anthropic Claude API Provider Adapter for BARRY Intelligence Engine."""

import logging
import time
from typing import Any, Dict

import httpx

from backend.ai.interface import LLMProvider
from backend.ai.prompts import (
    ANALYSIS_SYSTEM_PROMPT,
    REPORT_SYSTEM_PROMPT,
    build_analysis_messages,
    build_report_messages,
)
from backend.ai.providers.openrouter_provider import extract_json_object
from backend.ai.schemas import MissionAnalysis
from backend.config import get_settings

logger = logging.getLogger("barry.ai.anthropic")


class AnthropicProvider(LLMProvider):
    """Direct Anthropic Claude API provider adapter."""

    def __init__(
        self,
        api_key: str = "",
        model_name: str = "claude-3-5-haiku-latest",
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 30.0,
    ) -> None:
        settings = get_settings()
        self.provider_name = "anthropic"
        self.api_key = (api_key or settings.ANTHROPIC_API_KEY).strip()
        self.model_name = model_name or settings.ANTHROPIC_MODEL
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout or settings.AI_TIMEOUT

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    async def analyze_mission(self, mission_data: Dict[str, Any]) -> Dict[str, Any]:
        messages = build_analysis_messages(mission_data)
        user_msgs = [m for m in messages if m["role"] != "system"]
        payload = {
            "model": self.model_name,
            "max_tokens": 1024,
            "system": ANALYSIS_SYSTEM_PROMPT,
            "messages": user_msgs,
            "temperature": 0.1,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        blocks = data.get("content", [])
        text_content = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        parsed = extract_json_object(text_content)
        validated = MissionAnalysis.model_validate(parsed)
        return validated.model_dump()

    async def generate_report(self, mission_data: Dict[str, Any]) -> str:
        messages = build_report_messages(mission_data)
        user_msgs = [m for m in messages if m["role"] != "system"]
        payload = {
            "model": self.model_name,
            "max_tokens": 1500,
            "system": REPORT_SYSTEM_PROMPT,
            "messages": user_msgs,
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        blocks = data.get("content", [])
        text_content = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return text_content.strip()

    async def health_check(self) -> Dict[str, Any]:
        configured = bool(self.api_key)
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "available": configured,
            "details": "Anthropic API key configured." if configured else "ANTHROPIC_API_KEY is not configured.",
        }
