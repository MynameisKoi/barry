"""Direct Google Gemini API Provider Adapter for BARRY Intelligence Engine."""

import logging
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

logger = logging.getLogger("barry.ai.google")


class GoogleProvider(LLMProvider):
    """Direct Google Gemini API provider adapter."""

    def __init__(
        self,
        api_key: str = "",
        model_name: str = "gemini-2.0-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 30.0,
    ) -> None:
        settings = get_settings()
        self.provider_name = "google"
        self.api_key = (api_key or settings.GOOGLE_API_KEY).strip()
        self.model_name = model_name or settings.GOOGLE_MODEL
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout or settings.AI_TIMEOUT

    async def analyze_mission(self, mission_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("GOOGLE_API_KEY is not configured.")

        messages = build_analysis_messages(mission_data)
        user_text = messages[-1]["content"]
        payload = {
            "systemInstruction": {"parts": [{"text": ANALYSIS_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        url = f"{self.base_url}/models/{self.model_name}:generateContent"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                params={"key": self.api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text_content = "".join(p.get("text", "") for p in parts)
        parsed = extract_json_object(text_content)
        validated = MissionAnalysis.model_validate(parsed)
        return validated.model_dump()

    async def generate_report(self, mission_data: Dict[str, Any]) -> str:
        if not self.api_key:
            raise RuntimeError("GOOGLE_API_KEY is not configured.")

        messages = build_report_messages(mission_data)
        user_text = messages[-1]["content"]
        payload = {
            "systemInstruction": {"parts": [{"text": REPORT_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {"temperature": 0.2},
        }
        url = f"{self.base_url}/models/{self.model_name}:generateContent"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                params={"key": self.api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text_content = "".join(p.get("text", "") for p in parts)
        return text_content.strip()

    async def health_check(self) -> Dict[str, Any]:
        configured = bool(self.api_key)
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "available": configured,
            "details": "Google Gemini API key configured." if configured else "GOOGLE_API_KEY is not configured.",
        }
