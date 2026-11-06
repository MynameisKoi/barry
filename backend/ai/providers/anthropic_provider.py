"""Direct Anthropic Claude API Provider Adapter for BARRY Intelligence Engine."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

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

LOGS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "logs"
LOGS_FILE = LOGS_DIR / "ai_calls.jsonl"


def _record_api_call(record: Dict[str, Any]) -> None:
    """Appends an API call record to logs/ai_calls.jsonl for complete transparency."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning("Failed to append AI call log to file: %s", exc)


class AnthropicProvider(LLMProvider):
    """Direct Anthropic Claude API provider adapter with transparent audit logging."""

    def __init__(
        self,
        api_key: str = "",
        model_name: str = "claude-haiku-5-5",
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 60.0,
    ) -> None:
        settings = get_settings()
        self.provider_name = "anthropic"
        self.api_key = (api_key or settings.ANTHROPIC_API_KEY).strip()
        self.model_name = model_name or settings.ANTHROPIC_MODEL or "claude-haiku-5-5"
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout or settings.AI_TIMEOUT or 60.0

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    async def analyze_mission(self, mission_data: Dict[str, Any]) -> Dict[str, Any]:
        start_ts = time.monotonic()
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        messages = build_analysis_messages(mission_data)
        user_msgs = [m for m in messages if m["role"] != "system"]
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": 4096,
            "system": ANALYSIS_SYSTEM_PROMPT,
            "messages": user_msgs,
        }
        # Newer Anthropic models (like claude-haiku-5-5) deprecate the temperature parameter.
        if not any(k in self.model_name for k in ("haiku-5", "claude-haiku-5")):
            payload["temperature"] = 0.1

        logger.info(
            "Anthropic API Call [analyze_mission] -> model=%s, event_type=%s, zone=%s",
            self.model_name,
            mission_data.get("event_type"),
            mission_data.get("data", {}).get("zone") or mission_data.get("zone"),
        )

        log_entry: Dict[str, Any] = {
            "timestamp": timestamp_iso,
            "provider": "anthropic",
            "model": self.model_name,
            "call_type": "analyze_mission",
            "input_preview": str(mission_data)[:300],
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/messages",
                    headers=self._headers(),
                    json=payload,
                )
                duration_ms = round((time.monotonic() - start_ts) * 1000, 1)
                log_entry["duration_ms"] = duration_ms
                log_entry["http_status"] = resp.status_code

                if resp.status_code != 200:
                    err_text = resp.text
                    log_entry["status"] = "error"
                    log_entry["error"] = err_text
                    _record_api_call(log_entry)
                    logger.error("Anthropic API error HTTP %d: %s", resp.status_code, err_text)
                    resp.raise_for_status()

                data = resp.json()
                usage = data.get("usage", {})
                log_entry["usage"] = usage
                log_entry["request_id"] = data.get("id")
                log_entry["status"] = "success"

            blocks = data.get("content", [])
            text_content = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            log_entry["response_preview"] = text_content[:300]
            _record_api_call(log_entry)

            logger.info(
                "Anthropic API Success [analyze_mission] in %.1f ms (input=%s, output=%s, thinking=%s)",
                duration_ms,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("output_tokens_details", {}).get("thinking_tokens", 0),
            )

            parsed = extract_json_object(text_content)
            validated = MissionAnalysis.model_validate(parsed)
            return validated.model_dump()

        except Exception as exc:
            duration_ms = round((time.monotonic() - start_ts) * 1000, 1)
            log_entry["duration_ms"] = duration_ms
            log_entry["status"] = "error"
            log_entry["error"] = str(exc)
            _record_api_call(log_entry)
            raise

    async def generate_report(self, mission_data: Dict[str, Any]) -> str:
        start_ts = time.monotonic()
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        messages = build_report_messages(mission_data)
        user_msgs = [m for m in messages if m["role"] != "system"]
        payload: Dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": 4096,
            "system": REPORT_SYSTEM_PROMPT,
            "messages": user_msgs,
        }
        # Newer Anthropic models (like claude-haiku-5-5) deprecate the temperature parameter.
        if not any(k in self.model_name for k in ("haiku-5", "claude-haiku-5")):
            payload["temperature"] = 0.2

        logger.info(
            "Anthropic API Call [generate_report] -> model=%s, mission_id=%s, events=%d",
            self.model_name,
            mission_data.get("mission_id"),
            len(mission_data.get("inspection_events", [])),
        )

        log_entry: Dict[str, Any] = {
            "timestamp": timestamp_iso,
            "provider": "anthropic",
            "model": self.model_name,
            "call_type": "generate_report",
            "mission_id": mission_data.get("mission_id"),
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/messages",
                    headers=self._headers(),
                    json=payload,
                )
                duration_ms = round((time.monotonic() - start_ts) * 1000, 1)
                log_entry["duration_ms"] = duration_ms
                log_entry["http_status"] = resp.status_code

                if resp.status_code != 200:
                    err_text = resp.text
                    log_entry["status"] = "error"
                    log_entry["error"] = err_text
                    _record_api_call(log_entry)
                    logger.error("Anthropic API error HTTP %d: %s", resp.status_code, err_text)
                    resp.raise_for_status()

                data = resp.json()
                usage = data.get("usage", {})
                log_entry["usage"] = usage
                log_entry["request_id"] = data.get("id")
                log_entry["status"] = "success"

            blocks = data.get("content", [])
            text_content = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            log_entry["response_preview"] = text_content[:300]
            _record_api_call(log_entry)

            logger.info(
                "Anthropic API Success [generate_report] in %.1f ms (input=%s, output=%s, thinking=%s)",
                duration_ms,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("output_tokens_details", {}).get("thinking_tokens", 0),
            )

            return text_content.strip()

        except Exception as exc:
            duration_ms = round((time.monotonic() - start_ts) * 1000, 1)
            log_entry["duration_ms"] = duration_ms
            log_entry["status"] = "error"
            log_entry["error"] = str(exc)
            _record_api_call(log_entry)
            raise

    async def health_check(self) -> Dict[str, Any]:
        configured = bool(self.api_key)
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "available": configured,
            "details": f"Anthropic Claude API configured with model {self.model_name}."
            if configured
            else "ANTHROPIC_API_KEY is not configured.",
        }
