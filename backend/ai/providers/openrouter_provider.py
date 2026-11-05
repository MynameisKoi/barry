"""OpenRouter LLM Provider for BARRY Intelligence Engine.

Uses asynchronous HTTPX requests to OpenRouter's chat completions endpoint
with configurable models, retry logic, timeout protection, and Pydantic validation.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
import httpx

from backend.ai.interface import LLMProvider
from backend.ai.prompts import build_analysis_messages, build_report_messages
from backend.ai.schemas import MissionAnalysis

logger = logging.getLogger("barry.ai.openrouter")


def extract_json_object(raw_text: str) -> Dict[str, Any]:
    """Safely extracts and parses a JSON object from LLM text output.

    Handles both raw JSON strings and Markdown-fenced code blocks.
    """
    cleaned = raw_text.strip()
    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        # Locate outermost JSON braces if surrounded by prose
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]

    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON is not an object.")
    return parsed


class OpenRouterProvider(LLMProvider):
    """Asynchronous OpenRouter gateway implementation of LLMProvider."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "openai/gpt-4o-mini",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 30.0,
        max_retries: int = 2,
        provider_label: str = "openrouter",
    ) -> None:
        self.provider_name = provider_label
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)

    def _build_headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise RuntimeError(
                f"{self.provider_name.upper()} API key is not configured."
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/MynameisKoi/barry",
            "X-Title": "BARRY Robotic Inspection System",
        }

    async def _chat_completion(
        self, messages: List[Dict[str, str]], temperature: float = 0.1
    ) -> str:
        """Executes chat completion with retry handling for transient errors."""
        headers = self._build_headers()
        url = f"{self.base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)

                if response.status_code in (401, 403):
                    raise RuntimeError(
                        f"{self.provider_name} authentication failed (HTTP {response.status_code})."
                    )

                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"Retryable HTTP {response.status_code} from {self.provider_name}",
                        request=response.request,
                        response=response,
                    )

                if response.status_code >= 400:
                    raise RuntimeError(
                        f"{self.provider_name} returned HTTP {response.status_code}: {response.text[:200]}"
                    )

                body = response.json()
                choices = body.get("choices")
                if not choices or not isinstance(choices, list):
                    raise ValueError(
                        f"Malformed response from {self.provider_name}: missing 'choices'."
                    )

                message = choices[0].get("message", {})
                content = message.get("content")
                if not content or not str(content).strip():
                    raise ValueError(
                        f"Empty or refused completion from {self.provider_name}."
                    )

                return str(content).strip()

            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.TransportError) as err:
                last_error = err
                logger.warning(
                    "%s request attempt %d/%d failed: %s",
                    self.provider_name,
                    attempt + 1,
                    self.max_retries + 1,
                    type(err).__name__,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(0.3 * (2**attempt))
                    continue
                break
            except Exception as err:
                # Non-retryable error (e.g., 401 auth, ValueError)
                logger.error("%s request failed: %s", self.provider_name, err)
                raise

        raise RuntimeError(
            f"{self.provider_name} failed after {self.max_retries + 1} attempts: {last_error}"
        )

    async def analyze_mission(
        self, mission_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyzes an inspection event or mission data and validates against MissionAnalysis."""
        messages = build_analysis_messages(mission_data)
        raw_output = await self._chat_completion(messages, temperature=0.1)
        parsed_dict = extract_json_object(raw_output)
        validated = MissionAnalysis.model_validate(parsed_dict)
        return validated.model_dump()

    async def generate_report(self, mission_data: Dict[str, Any]) -> str:
        """Generates a Markdown inspection report via OpenRouter."""
        messages = build_report_messages(mission_data)
        report_md = await self._chat_completion(messages, temperature=0.2)
        return report_md

    async def health_check(self) -> Dict[str, Any]:
        """Checks whether OpenRouter credentials are set and endpoint is reachable."""
        if not self.api_key:
            return {
                "provider": self.provider_name,
                "model": self.model_name,
                "available": False,
                "details": "OPENROUTER_API_KEY is not configured.",
            }

        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if resp.status_code == 200:
                return {
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "available": True,
                    "latency_ms": latency_ms,
                    "details": "OpenRouter API reachable and authenticated.",
                }
            return {
                "provider": self.provider_name,
                "model": self.model_name,
                "available": False,
                "latency_ms": latency_ms,
                "details": f"OpenRouter returned HTTP {resp.status_code}.",
            }
        except Exception as err:
            return {
                "provider": self.provider_name,
                "model": self.model_name,
                "available": False,
                "details": f"OpenRouter health check error: {type(err).__name__}",
            }
