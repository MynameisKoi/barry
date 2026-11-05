"""Central BARRY AI Intelligence Engine Orchestrator.

Coordinates provider invocation, fallback resilience, Pydantic schema validation,
SQLite persistence in `ai_analyses`, and structured mission report generation.
"""

from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional

from backend.ai.interface import LLMProvider
from backend.ai.provider_factory import get_fallback_provider, get_llm_provider
from backend.ai.providers.mock_provider import MockProvider
from backend.ai.schemas import (
    AIAnalysisRecord,
    AIProviderStatus,
    MissionAnalysis,
    MissionReport,
)
from backend.config import get_settings
from backend.database import DatabaseManager, db

logger = logging.getLogger("barry.ai.orchestrator")


class AIOrchestrator:
    """Coordinates LLM analysis, fallback execution, and SQLite storage."""

    def __init__(
        self,
        db_manager: DatabaseManager = db,
        primary_provider: Optional[LLMProvider] = None,
        fallback_provider: Optional[LLMProvider] = None,
        use_configured_fallback: bool = True,
    ) -> None:
        self.db = db_manager
        self._explicit_primary = primary_provider
        self._explicit_fallback = fallback_provider
        self._use_configured_fallback = use_configured_fallback

    def _get_primary(self) -> LLMProvider:
        if self._explicit_primary is not None:
            return self._explicit_primary
        return get_llm_provider()

    def _get_fallback(self) -> Optional[LLMProvider]:
        if self._explicit_fallback is not None:
            return self._explicit_fallback
        if not self._use_configured_fallback:
            return None
        return get_fallback_provider()

    async def analyze_inspection_event(
        self,
        event_payload: Dict[str, Any],
        mission_id: Optional[str] = None,
        event_id: Optional[int] = None,
    ) -> AIAnalysisRecord:
        """Analyzes an inspection event using the primary provider with fallback protection.

        Never raises unhandled provider exceptions so event ingestion and mission state
        are never interrupted.
        """
        resolved_mission_id = mission_id or event_payload.get("mission_id")
        resolved_event_id = (
            event_id
            if event_id is not None
            else event_payload.get("id") or event_payload.get("event_id")
        )

        primary = self._get_primary()
        fallback = self._get_fallback()

        start_ts = time.perf_counter()
        status = "completed"
        error_message: Optional[str] = None
        raw_result: Optional[Dict[str, Any]] = None
        provider_name = getattr(primary, "provider_name", "unknown")
        model_name = getattr(primary, "model_name", get_settings().LLM_MODEL)

        try:
            raw_result = await primary.analyze_mission(event_payload)
        except Exception as primary_exc:
            logger.warning(
                "Primary LLM provider (%s) failed: %s", provider_name, primary_exc
            )
            error_message = f"Primary provider error: {primary_exc}"

            if (
                fallback is not None
                and getattr(fallback, "provider_name", None) != provider_name
            ):
                try:
                    raw_result = await fallback.analyze_mission(event_payload)
                    provider_name = getattr(fallback, "provider_name", "fallback")
                    model_name = getattr(fallback, "model_name", "fallback-model")
                    status = "fallback"
                except Exception as fallback_exc:
                    logger.error("Fallback LLM provider failed: %s", fallback_exc)
                    error_message = f"{error_message} | Fallback error: {fallback_exc}"
                    status = "unavailable"
            else:
                status = "unavailable"

        latency_ms = round((time.perf_counter() - start_ts) * 1000.0, 2)

        if raw_result is not None:
            validated = MissionAnalysis.model_validate(raw_result)
        else:
            # Safe degraded intelligence object when all AI providers are unreachable
            validated = MissionAnalysis(
                risk_level="medium",
                summary="AI analysis unavailable due to provider error; manual operator verification required.",
                recommended_action="Inspect raw camera and telemetry data manually before proceeding.",
                requires_human_review=True,
                detected_objects=[],
                reasoning_summary="Observed fact: Event was recorded in SQLite. Interpretation: AI provider was unreachable.",
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        analysis_id = self._save_analysis(
            mission_id=resolved_mission_id,
            event_id=resolved_event_id,
            provider=provider_name,
            model=model_name,
            analysis=validated,
            created_at=now_iso,
            status=status,
            latency_ms=latency_ms,
        )

        return AIAnalysisRecord(
            id=analysis_id,
            mission_id=resolved_mission_id,
            event_id=resolved_event_id,
            provider=provider_name,
            model=model_name,
            risk_level=validated.risk_level,
            analysis=validated,
            created_at=now_iso,
            status=status,
            latency_ms=latency_ms,
            error_message=error_message,
        )

    def _save_analysis(
        self,
        *,
        mission_id: Optional[str],
        event_id: Optional[int],
        provider: str,
        model: str,
        analysis: MissionAnalysis,
        created_at: str,
        status: str,
        latency_ms: float,
    ) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Ensure mission_id/event_id foreign keys exist if provided
            valid_mission_id = mission_id
            if valid_mission_id:
                cursor.execute(
                    "SELECT mission_id FROM missions WHERE mission_id = ?",
                    (valid_mission_id,),
                )
                if not cursor.fetchone():
                    valid_mission_id = None

            valid_event_id = event_id
            if valid_event_id is not None:
                cursor.execute(
                    "SELECT id FROM inspection_events WHERE id = ?",
                    (valid_event_id,),
                )
                if not cursor.fetchone():
                    valid_event_id = None

            cursor.execute(
                """
                INSERT INTO ai_analyses (
                    mission_id, event_id, provider, model, risk_level,
                    analysis_json, created_at, status, latency_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    valid_mission_id,
                    valid_event_id,
                    provider,
                    model,
                    analysis.risk_level,
                    json.dumps(analysis.model_dump()),
                    created_at,
                    status,
                    latency_ms,
                ),
            )
            return int(cursor.lastrowid)

    def list_analyses(
        self,
        mission_id: Optional[str] = None,
        event_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if event_id is not None:
                cursor.execute(
                    "SELECT * FROM ai_analyses WHERE event_id = ? ORDER BY id DESC LIMIT ?",
                    (event_id, limit),
                )
            elif mission_id:
                cursor.execute(
                    "SELECT * FROM ai_analyses WHERE mission_id = ? ORDER BY id DESC LIMIT ?",
                    (mission_id, limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM ai_analyses ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            rows = [dict(r) for r in cursor.fetchall()]
            for row in rows:
                try:
                    row["analysis"] = json.loads(row.pop("analysis_json", "{}"))
                except Exception:
                    row["analysis"] = None
            return rows

    async def generate_mission_report(self, mission_id: str) -> MissionReport:
        """Generates a complete post-inspection MissionReport for `mission_id`."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT m.*, o.name AS operator_name, o.role AS operator_role
                FROM missions m
                LEFT JOIN operators o ON m.operator_id = o.operator_id
                WHERE m.mission_id = ?
                """,
                (mission_id.strip(),),
            )
            mission_row = cursor.fetchone()
            if not mission_row:
                raise ValueError(f"Mission '{mission_id}' not found.")
            mission = dict(mission_row)

            cursor.execute(
                "SELECT * FROM inspection_events WHERE mission_id = ? ORDER BY id ASC",
                (mission_id.strip(),),
            )
            insp_rows = [dict(r) for r in cursor.fetchall()]

            cursor.execute(
                "SELECT * FROM events WHERE mission_id = ? ORDER BY event_id ASC",
                (mission_id.strip(),),
            )
            audit_rows = [dict(r) for r in cursor.fetchall()]

            cursor.execute(
                "SELECT * FROM ai_analyses WHERE mission_id = ? ORDER BY id ASC",
                (mission_id.strip(),),
            )
            analysis_rows = [dict(r) for r in cursor.fetchall()]

        recorded_events: List[Dict[str, Any]] = []
        inspected_zones = {mission["zone"]} if mission.get("zone") else set()
        detected_objects: List[str] = []

        for r in insp_rows:
            try:
                edata = json.loads(r.get("event_data") or "{}")
            except Exception:
                edata = {}
            if isinstance(edata, dict):
                if edata.get("zone"):
                    inspected_zones.add(str(edata["zone"]))
                obj = edata.get("object") or edata.get("detected_object")
                if obj and str(obj) not in detected_objects:
                    detected_objects.append(str(obj))
            recorded_events.append(
                {
                    "id": r["id"],
                    "source": r["source"],
                    "event_type": r["event_type"],
                    "data": edata,
                    "timestamp": r["timestamp"],
                }
            )

        if not recorded_events:
            for r in audit_rows:
                recorded_events.append(
                    {
                        "id": r["event_id"],
                        "source": "system",
                        "event_type": r["event_type"],
                        "data": {"description": r["description"]},
                        "timestamp": r["timestamp"],
                    }
                )

        events_requiring_review: List[Dict[str, Any]] = []
        for ar in analysis_rows:
            try:
                parsed_an = json.loads(ar.get("analysis_json") or "{}")
            except Exception:
                parsed_an = {}
            if parsed_an.get("requires_human_review"):
                events_requiring_review.append(
                    {
                        "analysis_id": ar["id"],
                        "event_id": ar.get("event_id"),
                        "risk_level": ar["risk_level"],
                        "summary": parsed_an.get("summary", ""),
                        "recommended_action": parsed_an.get("recommended_action", ""),
                    }
                )

        mission_payload = {
            "mission_id": mission["mission_id"],
            "status": mission["status"],
            "operator_id": mission["operator_id"],
            "operator_name": mission.get("operator_name"),
            "zone": mission["zone"],
            "started_at": mission["started_at"],
            "ended_at": mission.get("ended_at"),
            "inspection_events": recorded_events,
        }

        primary = self._get_primary()
        fallback = self._get_fallback() or MockProvider()
        provider_name = getattr(primary, "provider_name", "mock")
        model_name = getattr(primary, "model_name", get_settings().LLM_MODEL)

        try:
            assessment_raw = await primary.analyze_mission(mission_payload)
            report_md = await primary.generate_report(mission_payload)
        except Exception as exc:
            logger.warning(
                "Primary provider report generation failed (%s), using fallback", exc
            )
            provider_name = getattr(fallback, "provider_name", "mock")
            model_name = getattr(fallback, "model_name", "barry-mock-v1")
            assessment_raw = await fallback.analyze_mission(mission_payload)
            report_md = await fallback.generate_report(mission_payload)

        validated_assessment = MissionAnalysis.model_validate(assessment_raw)
        for obj in validated_assessment.detected_objects:
            if obj not in detected_objects:
                detected_objects.append(obj)

        now_iso = datetime.now(timezone.utc).isoformat()
        return MissionReport(
            mission_id=mission["mission_id"],
            status=mission["status"],
            operator_id=mission["operator_id"],
            operator_name=mission.get("operator_name"),
            start_time=mission["started_at"],
            end_time=mission.get("ended_at"),
            inspected_zones=sorted(inspected_zones),
            recorded_events=recorded_events,
            detected_objects=detected_objects,
            ai_assessment=validated_assessment,
            report_markdown=report_md,
            events_requiring_human_review=events_requiring_review,
            provider=provider_name,
            model=model_name,
            generated_at=now_iso,
            limitations=(
                "AI assessment is based solely on recorded sensor and vision observations; "
                "all medium and high risk findings require human operator verification."
            ),
        )

    async def get_provider_status(self) -> AIProviderStatus:
        settings = get_settings()
        primary = self._get_primary()
        health = await primary.health_check()
        return AIProviderStatus(
            provider=health.get("provider", settings.LLM_PROVIDER),
            model=health.get("model", settings.LLM_MODEL),
            available=bool(health.get("available", False)),
            fallback_provider=settings.FALLBACK_LLM_PROVIDER or None,
            latency_ms=health.get("latency_ms"),
            details=str(health.get("details", "")),
        )
