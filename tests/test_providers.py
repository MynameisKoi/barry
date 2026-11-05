"""Unit tests for LLM providers, OpenRouter HTTP mocking, error handling, and provider factory."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from backend.ai.provider_factory import (
    create_provider_by_name,
    get_fallback_provider,
    get_llm_provider,
)
from backend.ai.providers.local_provider import LocalLLMProvider
from backend.ai.providers.mock_provider import MockProvider
from backend.ai.providers.openrouter_provider import (
    OpenRouterProvider,
    extract_json_object,
)
from backend.config import Settings


@pytest.mark.asyncio
async def test_mock_provider_deterministic_analysis_and_report():
    provider = MockProvider()
    health = await provider.health_check()
    assert health["available"] is True
    assert health["provider"] == "mock"

    # High risk hazard event
    crit_res = await provider.analyze_mission(
        {
            "event_type": "radiation",
            "data": {
                "object": "radiation",
                "zone": "Containment Room 1",
                "confidence": 0.98,
                "dose_rate_msv": 15.2,
            },
        }
    )
    assert crit_res["risk_level"] == "high"
    assert crit_res["requires_human_review"] is True
    assert "radiation" in crit_res["detected_objects"]

    # Routine low-risk event
    low_res = await provider.analyze_mission(
        {
            "event_type": "routine_waypoint_reached",
            "data": {"zone": "Hallway A", "confidence": 0.95},
        }
    )
    assert low_res["risk_level"] == "low"
    assert low_res["requires_human_review"] is False

    report_md = await provider.generate_report(
        {
            "mission_id": "MSN-TEST01",
            "status": "completed",
            "operator_name": "Alice Vance",
            "zone": "Sector 9",
            "inspection_events": [
                {
                    "timestamp": "2026-11-05T12:00:00Z",
                    "source": "dogzilla",
                    "event_type": "thermal_anomaly",
                    "data": {"object": "overheat", "zone": "Sector 9", "confidence": 0.85},
                }
            ],
        }
    )
    assert "# BARRY Inspection Report" in report_md
    assert "MSN-TEST01" in report_md


def test_extract_json_object_variants():
    raw_clean = '{"risk_level": "high", "summary": "Overheating valve"}'
    assert extract_json_object(raw_clean)["risk_level"] == "high"

    fenced = """Here is the analysis:
```json
{
  "risk_level": "high",
  "summary": "Gas leak detected"
}
```
"""
    assert extract_json_object(fenced)["risk_level"] == "high"

    with pytest.raises((ValueError, json.JSONDecodeError)):
        extract_json_object("No JSON object here at all")


@pytest.mark.asyncio
async def test_openrouter_provider_success_and_error_cases():
    provider = OpenRouterProvider(
        api_key="sk-or-test-dummy-key",
        model_name="openai/gpt-4o-mini",
        timeout=5.0,
        max_retries=1,
    )

    valid_llm_json = {
        "risk_level": "high",
        "summary": "Elevated temperature on primary coolant pump.",
        "recommended_action": "Dispatch maintenance crew and reduce pump load.",
        "requires_human_review": True,
        "detected_objects": ["coolant_pump"],
        "reasoning_summary": "Observed fact: Pump at 94C. Interpretation: Exceeds safe operating threshold.",
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "model": "openai/gpt-4o-mini",
        "choices": [{"message": {"content": json.dumps(valid_llm_json)}}],
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await provider.analyze_mission(
            {"event_type": "thermal_anomaly", "data": {"zone": "Pump Room"}}
        )
        assert result["risk_level"] == "high"
        assert result["requires_human_review"] is True

    # Test 401 invalid API key error handling
    unauthorized_resp = MagicMock()
    unauthorized_resp.status_code = 401
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = unauthorized_resp
        with pytest.raises(RuntimeError, match="authentication failed"):
            await provider.analyze_mission({"event_type": "test"})

    # Test 429 rate-limit retry then success
    rate_limit_resp = MagicMock()
    rate_limit_resp.status_code = 429
    rate_limit_resp.request = MagicMock()
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [rate_limit_resp, mock_response]
        result = await provider.analyze_mission({"event_type": "thermal_anomaly"})
        assert result["risk_level"] == "high"
        assert mock_post.call_count == 2

    # Test timeout handling
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Request timed out")
        with pytest.raises(RuntimeError, match="failed after"):
            await provider.analyze_mission({"event_type": "test"})

    # Test malformed JSON output from model
    malformed_resp = MagicMock()
    malformed_resp.status_code = 200
    malformed_resp.json.return_value = {
        "choices": [{"message": {"content": "This is not valid JSON output"}}],
    }
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = malformed_resp
        with pytest.raises((ValueError, json.JSONDecodeError)):
            await provider.analyze_mission({"event_type": "test"})


def test_provider_factory_selection():
    cfg_mock = Settings(LLM_PROVIDER="mock", FALLBACK_LLM_PROVIDER="")
    p_mock = get_llm_provider(cfg_mock)
    assert isinstance(p_mock, MockProvider)
    assert get_fallback_provider(cfg_mock) is None

    cfg_or = Settings(
        LLM_PROVIDER="openrouter",
        OPENROUTER_API_KEY="sk-or-test",
        LLM_MODEL="anthropic/claude-3.5-sonnet",
        FALLBACK_LLM_PROVIDER="mock",
    )
    p_or = get_llm_provider(cfg_or)
    assert isinstance(p_or, OpenRouterProvider)
    assert p_or.model_name == "anthropic/claude-3.5-sonnet"
    assert isinstance(get_fallback_provider(cfg_or), MockProvider)

    cfg_local = Settings(
        LLM_PROVIDER="local",
        LOCAL_LLM_BASE_URL="http://192.168.100.10:8000/v1",
        LOCAL_LLM_MODEL="nvidia/Llama-3.1-Nemotron-70B-Instruct",
    )
    p_local = get_llm_provider(cfg_local)
    assert isinstance(p_local, LocalLLMProvider)
    assert p_local.base_url == "http://192.168.100.10:8000/v1"

    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        create_provider_by_name("nonexistent_provider", cfg_mock)
