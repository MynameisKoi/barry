"""Deterministic Mock LLM Provider for BARRY Development & Testing.

Allows full application startup, event analysis, report generation, and
automated testing without requiring external API keys.
"""

from typing import Any, Dict, List
from backend.ai.interface import LLMProvider
from backend.ai.schemas import MissionAnalysis

HIGH_RISK_KEYWORDS = {
    "fire",
    "smoke",
    "gas",
    "gas_leak",
    "explosion",
    "intruder",
    "breach",
    "critical",
    "radiation",
}
MEDIUM_RISK_KEYWORDS = {
    "person",
    "human",
    "spill",
    "spill_hazard",
    "unauthorized_backpack",
    "backpack",
    "hazard_cone",
    "obstacle",
    "anomaly",
    "overheat",
}


class MockProvider(LLMProvider):
    """Rule-based deterministic provider conforming to the LLMProvider contract."""

    def __init__(self, model_name: str = "barry-mock-v1") -> None:
        self.provider_name = "mock"
        self.model_name = model_name

    async def analyze_mission(
        self, mission_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Deterministically analyzes an inspection event or mission snapshot."""
        data = mission_data.get("data", {})
        if not isinstance(data, dict):
            data = {"value": str(data)}

        event_type = str(mission_data.get("event_type", "observation"))
        zone = str(
            data.get("zone")
            or mission_data.get("zone")
            or "Unspecified Zone"
        )
        confidence = data.get("confidence")

        detected: List[str] = []
        raw_obj = data.get("object") or data.get("detected_object")
        if isinstance(raw_obj, str) and raw_obj.strip():
            detected.append(raw_obj.strip())
        elif isinstance(data.get("objects"), list):
            detected.extend(str(o) for o in data["objects"] if o)

        # Also inspect nested events if a mission-level payload was passed
        events_list = mission_data.get("inspection_events") or mission_data.get("events") or []
        if isinstance(events_list, list):
            for ev in events_list:
                if isinstance(ev, dict):
                    ev_data = ev.get("data", {})
                    if isinstance(ev_data, dict) and ev_data.get("object"):
                        obj_name = str(ev_data["object"])
                        if obj_name not in detected:
                            detected.append(obj_name)

        combined_tokens = {t.lower() for t in detected}
        combined_tokens.add(event_type.lower())
        for k, v in data.items():
            if isinstance(v, str):
                combined_tokens.add(v.lower())

        # Determine risk level
        if combined_tokens & HIGH_RISK_KEYWORDS:
            risk_level = "high"
            requires_review = True
            obj_desc = ", ".join(detected) if detected else event_type
            summary = f"High-priority hazard or anomaly ({obj_desc}) detected in {zone}."
            recommended_action = (
                "Immediately halt forward progression, maintain safe standoff distance, "
                "and escalate to human safety supervisor for verification."
            )
            reasoning = (
                f"Observed fact: Event '{event_type}' reported '{obj_desc}' in {zone}"
                + (f" with confidence {confidence}." if confidence is not None else ".")
                + " Interpretation: Matches high-risk safety criteria requiring immediate operator attention."
            )
        elif combined_tokens & MEDIUM_RISK_KEYWORDS or detected:
            risk_level = "medium"
            requires_review = True
            obj_desc = ", ".join(detected) if detected else event_type
            summary = f"A {obj_desc} was detected in {zone}."
            recommended_action = "Request operator verification via VR camera feed before continuing patrol."
            reasoning = (
                f"Observed fact: Sensor reported '{obj_desc}' in {zone}"
                + (f" (confidence: {confidence})." if confidence is not None else ".")
                + " Interpretation: The detection indicates presence in the inspection area, "
                "but available sensor data alone does not establish whether it is authorized."
            )
        else:
            risk_level = "low"
            requires_review = False
            summary = f"Routine {event_type} recorded in {zone} with no anomalies identified."
            recommended_action = "Continue standard inspection mission."
            reasoning = (
                f"Observed fact: Event '{event_type}' logged in {zone}. "
                "Interpretation: No hazardous objects or unauthorized indicators were present in the observation."
            )

        validated = MissionAnalysis(
            risk_level=risk_level,
            summary=summary,
            recommended_action=recommended_action,
            requires_human_review=requires_review,
            detected_objects=detected,
            reasoning_summary=reasoning,
        )
        return validated.model_dump()

    async def generate_report(self, mission_data: Dict[str, Any]) -> str:
        """Generates a structured Markdown inspection report separating facts from interpretations."""
        mission_id = mission_data.get("mission_id", "UNKNOWN")
        operator_name = mission_data.get("operator_name") or mission_data.get("operator_id", "Unknown Operator")
        zone = mission_data.get("zone", "Unspecified")
        status = mission_data.get("status", "unknown")
        started_at = mission_data.get("started_at", "N/A")
        ended_at = mission_data.get("ended_at") or "In Progress"
        insp_events = mission_data.get("inspection_events", [])

        lines = [
            f"# BARRY Inspection Report — Mission `{mission_id}`",
            "",
            "## 1. Mission Metadata (Recorded Facts)",
            f"- **Mission ID:** `{mission_id}`",
            f"- **Status:** `{status}`",
            f"- **Operator:** {operator_name}",
            f"- **Primary Zone:** {zone}",
            f"- **Start Time:** {started_at}",
            f"- **End Time:** {ended_at}",
            f"- **Total Structured Observations:** {len(insp_events)}",
            "",
            "## 2. Recorded Event Timeline (Factual Observations)",
        ]

        if not insp_events:
            lines.append("_No structured inspection events were recorded during this mission._")
        else:
            for ev in insp_events:
                ts = ev.get("timestamp", "N/A")
                src = ev.get("source", "unknown")
                etype = ev.get("event_type", "unknown")
                edata = ev.get("data", {})
                lines.append(f"- **`[{ts}]`** (`{src}` / `{etype}`): `{edata}`")

        assessment = await self.analyze_mission(mission_data)
        lines.extend(
            [
                "",
                "## 3. AI Intelligence Assessment (Interpretation & Recommendations)",
                f"- **Assessed Risk Level:** `{assessment['risk_level'].upper()}`",
                f"- **Executive Summary:** {assessment['summary']}",
                f"- **Recommended Operator Action:** {assessment['recommended_action']}",
                f"- **Requires Human Review:** `{'Yes' if assessment['requires_human_review'] else 'No'}`",
                f"- **Reasoning:** {assessment['reasoning_summary']}",
                "",
                "## 4. Provider Metadata & Limitations",
                f"- **AI Provider:** `{self.provider_name}` (`{self.model_name}`)",
                "- **Limitations:** Assessment is generated from discrete telemetry and vision snapshots; "
                "all medium/high risk findings require human operator verification.",
            ]
        )
        return "\n".join(lines)

    async def health_check(self) -> Dict[str, Any]:
        """Returns health status for MockProvider."""
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "available": True,
            "details": "MockProvider active and operational (deterministic mode).",
        }
