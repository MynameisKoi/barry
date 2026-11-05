"""Centralized Pydantic Schemas for BARRY Intelligence Engine."""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class MissionAnalysis(BaseModel):
    """Structured intelligence returned by LLM analysis of an inspection event or mission."""

    risk_level: Literal["low", "medium", "high"] = Field(
        ..., description="AI-assessed risk level (low, medium, high)"
    )
    summary: str = Field(
        ..., min_length=1, description="Plain-language summary of what was observed"
    )
    recommended_action: str = Field(
        ..., min_length=1, description="Suggested action for human operator"
    )
    requires_human_review: bool = Field(
        ..., description="Whether operator verification is required"
    )
    detected_objects: List[str] = Field(
        default_factory=list, description="List of objects or anomalies identified"
    )
    reasoning_summary: str = Field(
        ...,
        min_length=1,
        description="Explanation distinguishing observed facts from AI interpretation",
    )


class InspectionEventIngest(BaseModel):
    """Payload submitted by DOGZILLA or simulator to ingest an inspection observation."""

    event_uuid: Optional[str] = Field(
        default=None, description="Optional idempotency key to prevent duplicate ingestion"
    )
    mission_id: str = Field(..., min_length=1, description="Associated mission ID")
    source: str = Field(
        default="dogzilla", min_length=1, description="Source platform (e.g., dogzilla, simulator)"
    )
    event_type: str = Field(
        ..., min_length=1, description="Event category (e.g., object_detection, thermal, telemetry)"
    )
    data: Dict[str, Any] = Field(
        default_factory=dict, description="Structured observation payload"
    )
    auto_analyze: bool = Field(
        default=True, description="Automatically run AI analysis upon ingestion"
    )


class AIAnalysisRecord(BaseModel):
    """Stored AI analysis record linked to an inspection event or mission."""

    id: Optional[int] = None
    mission_id: Optional[str] = None
    event_id: Optional[int] = None
    provider: str
    model: str
    risk_level: Literal["low", "medium", "high"]
    analysis: Optional[MissionAnalysis] = None
    created_at: str
    status: str = Field(description="completed, fallback, or unavailable")
    latency_ms: Optional[float] = None
    error_message: Optional[str] = None


class InspectionEventResponse(BaseModel):
    """Response returned when ingesting or retrieving an inspection event."""

    id: int
    event_uuid: Optional[str] = None
    mission_id: str
    source: str
    event_type: str
    data: Dict[str, Any]
    timestamp: str
    ai_analysis: Optional[AIAnalysisRecord] = None


class AnalyzeEventRequest(BaseModel):
    """Direct request to analyze an inspection event or observation."""

    event_id: Optional[int] = Field(
        default=None, description="Existing inspection_events ID to analyze"
    )
    mission_id: Optional[str] = Field(default=None, description="Mission ID context")
    source: str = Field(default="dogzilla")
    event_type: str = Field(default="object_detection")
    data: Dict[str, Any] = Field(default_factory=dict)


class MissionSummary(BaseModel):
    """AI-generated summary of a mission's events."""

    mission_id: str
    total_events: int
    inspected_zones: List[str]
    detected_objects: List[str]
    highest_risk_level: Literal["low", "medium", "high", "none"]
    events_requiring_review: int
    overall_summary: str
    provider: str
    model: str


class MissionReport(BaseModel):
    """Comprehensive inspection report separating factual observations from AI intelligence."""

    mission_id: str
    status: str
    operator_id: str
    operator_name: Optional[str] = None
    start_time: str
    end_time: Optional[str] = None
    inspected_zones: List[str] = Field(default_factory=list)
    recorded_events: List[Dict[str, Any]] = Field(
        default_factory=list, description="Factual recorded events from SQLite"
    )
    detected_objects: List[str] = Field(default_factory=list)
    ai_assessment: MissionAnalysis
    report_markdown: str = Field(
        ..., description="Readable inspection report distinguishing facts from AI interpretation"
    )
    events_requiring_human_review: List[Dict[str, Any]] = Field(default_factory=list)
    provider: str
    model: str
    generated_at: str
    limitations: str


class AIProviderStatus(BaseModel):
    """Health and configuration status of the BARRY AI provider layer."""

    provider: str
    model: str
    available: bool
    fallback_provider: Optional[str] = None
    latency_ms: Optional[float] = None
    details: str


class AIAlert(BaseModel):
    """WebSocket payload broadcast to Meta Quest 3S when AI analysis completes."""

    type: Literal["ai_alert"] = "ai_alert"
    mission_id: Optional[str] = None
    event_id: Optional[int] = None
    risk_level: Literal["low", "medium", "high"]
    summary: str
    recommended_action: str
    requires_human_review: bool
    detected_objects: List[str] = Field(default_factory=list)
    provider: str
    model: str
    timestamp: str
