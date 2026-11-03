"""End-to-end integration test harness for BARRY.

Validates the complete 8-step lifecycle:
1. NFC Credential Authorization
2. Mission Commissioning
3. Quadruped Locomotion & Telemetry Tracking
4. Video Feed Streaming & Snapshot
5. AI Object Detection & Alert Broadcast
6. Emergency Stop Locomotion Lock
7. Locomotion Reset
8. Mission Conclusion & Audit Log Verification
"""

import pytest
from fastapi.testclient import TestClient
from backend.main import app


def test_full_mission_e2e_pipeline():
    with TestClient(app) as client:
        # Step 0: Ensure system starts in clean state
        client.post("/api/mission/stop")
        client.post("/api/robot/reset-emergency")

        # Step 1: Scan NFC Test Credential (OP-001 Alice Vance)
        auth_resp = client.post("/api/auth/scan", json={"tag_uid": "04:A2:3B:4C:5D:6E:7F"})
        assert auth_resp.status_code == 200
        auth_data = auth_resp.json()
        assert auth_data["authorized"] is True
        operator_id = auth_data["operator"]["operator_id"]
        assert operator_id == "OP-001"

        # Step 2: Start Authorized Mission
        mission_resp = client.post("/api/mission/start", json={"operator_id": operator_id, "zone": "Zone-A"})
        assert mission_resp.status_code == 200
        mission_data = mission_resp.json()
        assert mission_data["active"] is True
        assert mission_data["status"] == "in_progress"
        mission_id = mission_data["mission_id"]

        # Step 3: Issue Locomotion Commands
        move_resp = client.post("/api/robot/command", json={"action": "forward", "speed": 0.4})
        assert move_resp.status_code == 200
        assert move_resp.json()["success"] is True

        turn_resp = client.post("/api/robot/command", json={"action": "left", "speed": 0.2})
        assert turn_resp.status_code == 200

        # Query telemetry to confirm coordinate change
        telem_resp = client.get("/api/robot/status")
        assert telem_resp.status_code == 200
        telem = telem_resp.json()
        assert telem["position"]["x"] > 0.0
        assert telem["position"]["heading"] == 15.0

        # Step 4: Verify Video Feed & Snapshot
        snap_resp = client.get("/api/camera/snapshot")
        assert snap_resp.status_code == 200
        assert snap_resp.headers["content-type"] == "image/jpeg"
        assert len(snap_resp.content) > 1000

        # Step 5: Trigger AI Object Detection
        det_resp = client.post("/api/ai/trigger-detection?zone=Zone-A")
        assert det_resp.status_code == 200
        det_data = det_resp.json()
        assert det_data["type"] == "detection"
        assert det_data["confidence"] >= 0.50

        # Step 6: Trigger Emergency Stop & Verify Locomotion Block
        estop_resp = client.post("/api/robot/command", json={"action": "emergency_stop", "speed": 0.0})
        assert estop_resp.status_code == 200

        # Movement attempt MUST fail with 400 when emergency stopped
        blocked_resp = client.post("/api/robot/command", json={"action": "forward", "speed": 0.2})
        assert blocked_resp.status_code == 400
        assert "blocked" in blocked_resp.json()["detail"].lower()

        # Step 7: Reset Emergency Stop
        reset_resp = client.post("/api/robot/reset-emergency")
        assert reset_resp.status_code == 200
        resumed_resp = client.post("/api/robot/command", json={"action": "stop", "speed": 0.0})
        assert resumed_resp.status_code == 200

        # Step 8: Conclude Mission & Verify Audit Log
        stop_resp = client.post("/api/mission/stop", json={"reason": "Inspection sweep completed"})
        assert stop_resp.status_code == 200
        assert stop_resp.json()["active"] is False

        # Query events log for this mission
        events_resp = client.get(f"/api/events?mission_id={mission_id}")
        assert events_resp.status_code == 200
        events = events_resp.json()
        event_types = [e["event_type"] for e in events]
        assert "mission_start" in event_types
        assert "robot_command" in event_types
        assert "emergency_stop" in event_types
        assert "mission_stop" in event_types
