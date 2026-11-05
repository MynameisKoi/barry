from backend.ai.orchestrator import AIOrchestrator
from backend.ai.schemas import MissionReport


class MissionReportGenerator:
    """Service wrapper for generating AI-assisted BARRY mission inspection reports."""

    def __init__(self, orchestrator: AIOrchestrator):
        self.orchestrator = orchestrator

    async def generate(self, mission_id: str) -> MissionReport:
        return await self.orchestrator.generate_mission_report(mission_id)
