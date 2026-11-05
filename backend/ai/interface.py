"""Abstract LLM Provider Interface for BARRY Intelligence Engine."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class LLMProvider(ABC):
    """Common interface implemented by all LLM providers (OpenRouter, Mock, Direct APIs, Local DGX)."""

    provider_name: str = "unknown"
    model_name: str = "unknown"

    @abstractmethod
    async def analyze_mission(
        self, mission_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyzes an inspection event or mission payload and returns structured intelligence.

        Must return a dictionary conforming to the MissionAnalysis schema:
          - risk_level: 'low' | 'medium' | 'high'
          - summary: str
          - recommended_action: str
          - requires_human_review: bool
          - detected_objects: list[str]
          - reasoning_summary: str
        """
        raise NotImplementedError

    @abstractmethod
    async def generate_report(
        self, mission_data: Dict[str, Any]
    ) -> str:
        """Generates a human-readable Markdown inspection report for a mission."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Checks provider readiness and configuration status."""
        raise NotImplementedError
