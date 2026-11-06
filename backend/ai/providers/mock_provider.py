"""Deterministic Mock LLM Provider for BARRY Development & Testing.

Allows full application startup, event analysis, report generation, and
automated testing without requiring external API keys. Incorporates:
- DOGZILLA S1/S2 hardware telemetry & kinematic stability rules (IMU roll/pitch, battery, 12-servo motor limits).
- Temporal multi-event correlation across `recent_mission_context` (escalating compound anomalies).
"""

from typing import Any, Dict, List, Optional
from backend.ai.interface import LLMProvider
from backend.ai.schemas import MissionAnalysis
from backend.robot.DOGZILLALib import PARAM

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
    "tip_over",
    "motor_stall",
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

    @staticmethod
    def _extract_numeric(container: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            val = container.get(k)
            if isinstance(val, (int, float)):
                return float(val)
        telem = container.get("telemetry")
        if isinstance(telem, dict):
            for k in keys:
                val = telem.get(k)
                if isinstance(val, (int, float)):
                    return float(val)
            imu = telem.get("imu")
            if isinstance(imu, dict):
                for k in keys:
                    val = imu.get(k)
                    if isinstance(val, (int, float)):
                        return float(val)
        return None

    @staticmethod
    def _check_motor_limit_saturation(data: Dict[str, Any]) -> Optional[str]:
        """Checks if any of DOGZILLA's 12 servo angles exceed or saturate PARAM['MOTOR_LIMIT']."""
        angles = data.get("motor_angles")
        if not isinstance(angles, list) or not angles:
            telem = data.get("telemetry")
            if isinstance(telem, dict):
                angles = telem.get("motor_angles")
        if not isinstance(angles, list) or len(angles) != 12:
            return None

        limits = PARAM["MOTOR_LIMIT"]  # [[-73, 57], [-66, 93], [-31, 31]]
        for idx, raw_angle in enumerate(angles):
            if not isinstance(raw_angle, (int, float)):
                continue
            low_lim, high_lim = limits[idx % 3]
            if raw_angle <= low_lim + 1.0 or raw_angle >= high_lim - 1.0:
                leg_num = (idx // 3) + 1
                joint_num = (idx % 3) + 1
                return f"leg_{leg_num}_joint_{joint_num}_limit({raw_angle}°)"
        return None

    async def analyze_mission(
        self, mission_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Deterministically analyzes an inspection event or mission snapshot, including DOGZILLA IMU/battery/servo telemetry and temporal context."""
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

        # Check DOGZILLA hardware IMU tilt (ATTITUDE_LIMIT: Roll ±20°, Pitch ±15°), Battery (0..100%), and 12-servo limits
        roll_deg = self._extract_numeric(data, "roll", "imu_roll")
        pitch_deg = self._extract_numeric(data, "pitch", "imu_pitch")
        battery_pct = self._extract_numeric(data, "battery", "battery_level")
        saturated_joint = self._check_motor_limit_saturation(data)

        imu_hazard = False
        if (roll_deg is not None and abs(roll_deg) >= 15.0) or (
            pitch_deg is not None and abs(pitch_deg) >= 12.0
        ):
            imu_hazard = True
            if "imu_tilt_instability" not in detected:
                detected.append("imu_tilt_instability")

        low_battery_hazard = False
        if battery_pct is not None and battery_pct <= 15.0:
            low_battery_hazard = True
            if "critical_low_battery" not in detected:
                detected.append("critical_low_battery")

        if saturated_joint and saturated_joint not in detected:
            detected.append(saturated_joint)

        # Check recent_mission_context for temporal correlation / compound anomaly escalation
        recent_context = mission_data.get("recent_mission_context") or []
        prior_medium_or_high = [
            ev
            for ev in recent_context
            if isinstance(ev, dict) and ev.get("risk_level") in ("medium", "high")
        ]

        combined_tokens = {t.lower() for t in detected}
        combined_tokens.add(event_type.lower())
        for k, v in data.items():
            if isinstance(v, str):
                combined_tokens.add(v.lower())

        # Determine risk level
        if imu_hazard:
            risk_level = "high"
            requires_review = True
            summary = (
                f"DOGZILLA IMU instability warning in {zone} "
                f"(roll={roll_deg}°, pitch={pitch_deg}° approaching ATTITUDE_LIMIT [±20°, ±15°])."
            )
            recommended_action = (
                "Halt locomotion immediately, enable onboard IMU self-stabilization (imu=1), "
                "and lower body posture to crouch height (z=75mm, pace=slow) to prevent tip-over."
            )
            reasoning = (
                f"Observed fact: DOGZILLA IMU reported roll={roll_deg}°, pitch={pitch_deg}° in {zone}. "
                "Interpretation: Exceeds safe quadruped stability margin near hardware limits (±20° roll, ±15° pitch)."
            )
        elif saturated_joint:
            risk_level = "high"
            requires_review = True
            summary = f"DOGZILLA servo joint limit saturation ({saturated_joint}) detected in {zone}."
            recommended_action = (
                "Stop movement immediately and execute dog.reset() or inspect leg clearance for mechanical obstruction."
            )
            reasoning = (
                f"Observed fact: Servo telemetry reported {saturated_joint} at hardware boundary MOTOR_LIMIT. "
                "Interpretation: High risk of servo stall or leg entanglement."
            )
        elif low_battery_hazard:
            risk_level = "high"
            requires_review = True
            summary = f"Critical DOGZILLA battery depletion ({battery_pct}%) reported in {zone}."
            recommended_action = (
                "Suspend inspection patrol, switch to normal standing posture, and return robot to charging station."
            )
            reasoning = (
                f"Observed fact: Battery register (0x01) reported {battery_pct}% in {zone}. "
                "Interpretation: Insufficient reserve for sustained 12-servo locomotion."
            )
        elif combined_tokens & HIGH_RISK_KEYWORDS:
            risk_level = "high"
            requires_review = True
            obj_desc = ", ".join(detected) if detected else event_type
            summary = f"High-priority hazard or anomaly ({obj_desc}) detected in {zone}."
            recommended_action = (
                "Immediately halt forward progression, maintain safe standoff distance, "
                "pitch camera body (attitude pitch/yaw) for remote visual confirmation, and escalate to safety supervisor."
            )
            reasoning = (
                f"Observed fact: Event '{event_type}' reported '{obj_desc}' in {zone}"
                + (f" with confidence {confidence}." if confidence is not None else ".")
                + " Interpretation: Matches high-risk safety criteria requiring immediate operator attention."
            )
        elif combined_tokens & MEDIUM_RISK_KEYWORDS or detected:
            obj_desc = ", ".join(detected) if detected else event_type
            # Escalate to high if there are already >= 2 prior medium/high anomalies in this mission
            if len(prior_medium_or_high) >= 2:
                risk_level = "high"
                requires_review = True
                prior_types = ", ".join(str(p.get("event_type", "anomaly")) for p in prior_medium_or_high[:3])
                summary = (
                    f"Compound mission hazard in {zone}: '{obj_desc}' follows {len(prior_medium_or_high)} "
                    f"prior anomalies ({prior_types})."
                )
                recommended_action = (
                    "Halt robot progression and initiate full operator review; multiple correlated anomalies "
                    "have accumulated during this mission."
                )
                reasoning = (
                    f"Observed fact: '{obj_desc}' detected in {zone} after {len(prior_medium_or_high)} prior "
                    f"elevated events ({prior_types}). Interpretation: Temporal correlation indicates an escalating "
                    "facility or operational hazard."
                )
            else:
                risk_level = "medium"
                requires_review = True
                summary = f"A {obj_desc} was detected in {zone}."
                if "obstacle" in combined_tokens:
                    recommended_action = (
                        "Verify clearance via VR feed; if passable, use DOGZILLA obstacle_crossing preset "
                        "(gait_type='high_walk', pace='slow', height_z=95mm) or strafe around the obstacle."
                    )
                else:
                    recommended_action = (
                        "Request operator verification via VR camera feed (adjust body attitude pitch/yaw if needed) "
                        "before continuing patrol."
                    )
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
                "Interpretation: No hazardous objects, IMU instability, or unauthorized indicators were present."
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
            "details": "MockProvider active and operational (deterministic mode with DOGZILLA telemetry & temporal correlation).",
        }
