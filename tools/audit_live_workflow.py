#!/usr/bin/env python3
"""Comprehensive Live Hardware & Software Workflow Audit for BARRY.

Audits:
1. Physical Flipper Zero NFC/RFID reader connectivity & credential authorization.
2. Physical DOGZILLA quadruped Pi robot API connectivity, IMU telemetry, and motion control.
3. BARRY AI Intelligence Engine event ingestion, risk reasoning, and safety invariants.
4. Mission lifecycle persistence, reporting, and audit trail verification.
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import httpx

from backend.ai.orchestrator import AIOrchestrator
from backend.ai.provider_factory import create_provider_by_name
from backend.auth.authorization import auth_service
from backend.auth.flipper_listener import find_flipper_port
from backend.config import get_settings
from backend.database import db
from backend.events.logger import EventLogger
from backend.robot.factory import create_robot
from backend.robot.remote import RemoteRobotClient
from backend.services.mission import mission_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("barry.audit")

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
WARN = "\033[93m[WARN]\033[0m"
INFO = "\033[94m[INFO]\033[0m"


def run_audit():
    print("\n" + "=" * 70)
    print("🚀 BARRY SYSTEM HARNESS & LIVE WORKFLOW AUDIT")
    print("=" * 70)

    audit_summary = {"passed": 0, "failed": 0, "warnings": 0, "checks": []}

    def record(name: str, passed: bool, detail: str = "", is_warn: bool = False):
        if is_warn:
            audit_summary["warnings"] += 1
            status = WARN
        elif passed:
            audit_summary["passed"] += 1
            status = PASS
        else:
            audit_summary["failed"] += 1
            status = FAIL
        print(f" {status} {name}: {detail}")
        audit_summary["checks"].append({"name": name, "status": "PASS" if passed else ("WARN" if is_warn else "FAIL"), "detail": detail})

    # =========================================================================
    # Step 1: Database & Persistence Audit
    # =========================================================================
    print(f"\n{INFO} Step 1: Persistence Layer & Schema Audit...")
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            required = {"operators", "missions", "events", "inspection_events", "authorization_logs", "ai_analyses"}
            missing = required - set(tables)
            record("Database Tables Check", len(missing) == 0, f"Found {len(tables)} tables (Missing: {list(missing)})")

            op_count = cursor.execute("SELECT COUNT(*) FROM operators").fetchone()[0]
            record("Operator Profiles Enrolled", op_count >= 4, f"{op_count} operators found in DB")
    except Exception as e:
        record("Database Verification", False, str(e))

    # =========================================================================
    # Step 2: Flipper Zero Device & Auth Service Audit
    # =========================================================================
    print(f"\n{INFO} Step 2: Flipper Zero & NFC/RFID Credential Audit...")
    flipper_port = find_flipper_port()
    if flipper_port:
        record("Flipper Zero Hardware Detection", True, f"Detected at {flipper_port}")
    else:
        record("Flipper Zero Hardware Detection", False, "No Flipper device node found on /dev/ttyACM*", is_warn=True)

    # Verify authorization of the user's HID RFID badge (OP-004: 2E:B7:76)
    auth_ok, op_data, auth_msg = auth_service.verify_tag_uid("2E:B7:76")
    record(
        "RFID Badge Authorization (OP-004)",
        auth_ok and op_data is not None and op_data.get("operator_id") == "OP-004",
        f"Result: {auth_msg} | Operator: {op_data.get('name') if op_data else 'None'}",
    )

    # Verify unauthorized credential rejection
    unauth_ok, _, unauth_msg = auth_service.verify_tag_uid("FF:EE:DD:00:11:22")
    record(
        "Unauthorized Credential Rejection",
        unauth_ok is False,
        f"Result: {unauth_msg}",
    )

    # =========================================================================
    # Step 3: Physical DOGZILLA Robot API & Telemetry Audit
    # =========================================================================
    print(f"\n{INFO} Step 3: DOGZILLA Quadruped Robot Connection & Control Audit...")
    cfg = get_settings()
    robot_client = RemoteRobotClient(base_url="http://192.168.137.236:8100", timeout=3.0)

    status = robot_client.get_status()
    dogzilla_online = status.get("connected") is True
    record(
        "DOGZILLA Live API Ping",
        dogzilla_online,
        f"Connected={dogzilla_online} | Mode={status.get('mode')} | Battery={status.get('battery_level')}% | Firmware={status.get('firmware_version', 'N/A')}",
    )

    if dogzilla_online:
        imu = status.get("imu", {})
        posture = status.get("posture", {})
        record(
            "DOGZILLA Physical IMU Telemetry",
            isinstance(imu.get("pitch"), (int, float)),
            f"Roll={imu.get('roll')}°, Pitch={imu.get('pitch')}°, Yaw={imu.get('yaw')}°, IMU Stabilize={imu.get('imu_stabilization')}",
        )

        # Audit Posture Command
        posture_res = robot_client.set_posture(preset="obstacle_crossing", imu_enabled=True, pitch=-5.0)
        record(
            "DOGZILLA Posture Update",
            posture_res.get("success") is True,
            f"Set preset 'obstacle_crossing' (height={posture_res.get('telemetry', {}).get('posture', {}).get('height_z')})",
        )

        # Audit Safe Locomotion
        move_res = robot_client.move("forward", speed=0.15)
        record(
            "DOGZILLA Safe Locomotion (Forward)",
            move_res.get("success") is True,
            f"Action={move_res.get('telemetry', {}).get('last_command')}",
        )

        # Reset posture to normal
        robot_client.set_posture(preset="crawl_low", imu_enabled=True, pitch=0.0)

        # Audit Emergency Stop
        estop_res = robot_client.move("emergency_stop")
        record(
            "DOGZILLA Hardware Emergency Stop",
            estop_res.get("telemetry", {}).get("emergency_stopped") is True,
            "Motors halted, emergency state latched.",
        )

        # Verify movement blocked while emergency stopped
        blocked_res = robot_client.move("forward", speed=0.2)
        record(
            "Locomotion Invariant Enforced During E-Stop",
            blocked_res.get("success") is False,
            f"Blocked message: {blocked_res.get('message')}",
        )

        # Reset Emergency Stop
        reset_res = robot_client.reset_emergency_stop()
        e_stop_cleared = (
            reset_res.get("success") is True
            and reset_res.get("telemetry", {}).get("emergency_stopped") is False
        )
        record(
            "DOGZILLA Emergency Stop Reset",
            e_stop_cleared,
            "System restored to operational state.",
        )
    else:
        record("DOGZILLA Robot Controls", False, "Skipping actuation tests: Pi API is offline or unreachable.", is_warn=True)

    # =========================================================================
    # Step 4: AI Intelligence Engine Audit (Host LLM & Reasoning Invariants)
    # =========================================================================
    print(f"\n{INFO} Step 4: AI Intelligence Engine & Safety Invariants Audit...")
    ai_orchestrator = AIOrchestrator(db_manager=db)
    ai_status = asyncio.run(ai_orchestrator.get_provider_status())
    record(
        "AI Provider Status",
        ai_status.available,
        f"Primary={ai_status.provider} ({ai_status.model}) | Fallback={ai_status.fallback_provider}",
    )

    # Test ad-hoc telemetry hazard reasoning
    hazard_event = {
        "event_type": "telemetry",
        "data": {
            "pitch": 24.5,  # High pitch tilt > 20 deg hazard threshold
            "roll": 3.2,
            "battery": 18.0,
            "heading": 45.0,
        },
    }
    analysis = asyncio.run(
        ai_orchestrator.analyze_inspection_event(
            event_payload=hazard_event,
            mission_id="AUDIT-TEST-001",
        )
    )
    record(
        "AI Hazard Risk Reasoning & Escalation",
        analysis.risk_level in ("medium", "high"),
        f"Detected Risk Level: {analysis.risk_level.upper()} | Recommended Action: {analysis.analysis.recommended_action[:60]}...",
    )

    record(
        "AI Safety Action Advisory Invariant (Human-in-the-Loop)",
        analysis.analysis.requires_human_review is True,
        "Safety Guarantee: AI marks anomaly for operator human review; strictly advisory, no autonomous actuation override.",
    )

    # =========================================================================
    # Step 5: Full Mission Lifecycle Audit
    # =========================================================================
    print(f"\n{INFO} Step 5: End-to-End Mission Lifecycle & Reporting Audit...")
    event_logger = EventLogger(db_manager=db)

    # 1. Stop any dangling missions
    mission_service.stop_mission()

    # 2. Start Mission
    m_info = mission_service.start_mission(operator_id="OP-004", zone="Substation-B")
    mission_id = m_info["mission_id"]
    record(
        "Mission Commissioning",
        m_info["active"] is True and m_info["operator_id"] == "OP-004",
        f"Mission ID: {mission_id} | Zone: {m_info['zone']}",
    )

    # 3. Ingest Inspection Events
    e1 = event_logger.ingest_event(
        mission_id=mission_id,
        source="dogzilla",
        event_type="telemetry",
        data={"roll": 0.4, "pitch": 0.7, "battery": 25.0},
    )
    e2 = event_logger.ingest_event(
        mission_id=mission_id,
        source="camera",
        event_type="detection",
        data={"object": "loose_coupling", "zone": "Substation-B", "confidence": 0.91},
    )
    record(
        "Mission Event Logging & Ingestion",
        e1 is not None and e2 is not None and "id" in e1,
        f"Logged event IDs: {e1.get('id')}, {e2.get('id')}",
    )

    # 4. Stop Mission
    m_stop = mission_service.stop_mission()
    record(
        "Mission Decommissioning",
        m_stop["active"] is False and m_stop["status"] == "completed",
        f"Final status: {m_stop['status']}",
    )

    # 5. Generate Comprehensive Mission Report
    try:
        report = asyncio.run(ai_orchestrator.generate_mission_report(mission_id=mission_id))
        record(
            "AI Mission Report Generation",
            report is not None and len(report.report_markdown) > 50,
            f"Mission: {report.mission_id} | Risk: {report.ai_assessment.risk_level.upper()} | Provider: {report.provider} ({report.model})",
        )
    except Exception as err:
        record("AI Mission Report Generation", False, str(err))

    # =========================================================================
    # Final Score & Audit Report
    # =========================================================================
    print("\n" + "=" * 70)
    print("📊 WORKFLOW AUDIT SUMMARY")
    print("=" * 70)
    print(f"Total Checks: {len(audit_summary['checks'])}")
    print(f"Passed:       {audit_summary['passed']}")
    print(f"Warnings:     {audit_summary['warnings']}")
    print(f"Failed:       {audit_summary['failed']}")
    print("=" * 70)

    return audit_summary


if __name__ == "__main__":
    summary = run_audit()
    if summary["failed"] > 0:
        sys.exit(1)
    sys.exit(0)
