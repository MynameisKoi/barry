"""Tests for NFC authorization module and API endpoints."""

import pytest
from fastapi.testclient import TestClient
from backend.auth.authorization import AuthService, normalize_uid
from backend.database import DatabaseManager
from backend.main import app


@pytest.fixture
def test_db(tmp_path):
    """Creates an isolated temporary database for test execution."""
    db_file = str(tmp_path / "test_auth.db")
    mgr = DatabaseManager(db_path=db_file)
    return mgr


def test_normalize_uid():
    """Verifies that NFC and RFID UIDs are normalized consistently."""
    assert normalize_uid("04:a2:3b:4c:5d:6e:7f") == "04:A2:3B:4C:5D:6E:7F"
    assert normalize_uid("04-A2-3B-4C-5D-6E-7F") == "04:A2:3B:4C:5D:6E:7F"
    assert normalize_uid("04 A2 3B 4C 5D 6E 7F") == "04:A2:3B:4C:5D:6E:7F"
    assert normalize_uid("04a23b4c5d6e7f") == "04:A2:3B:4C:5D:6E:7F"
    # 3-byte HID H10301 RFID card
    assert normalize_uid("2E B7 76") == "2E:B7:76"
    assert normalize_uid("2eb776") == "2E:B7:76"
    assert normalize_uid("2E:B7:76") == "2E:B7:76"


def test_auth_service_hid_card(test_db):
    """Verifies that 3-byte HID H10301 cards can be registered and verified in any format."""
    service = AuthService(db_manager=test_db)
    service.register_operator("OP-004", "Test Lead", "Commander", "2E:B7:76")

    # Match via spaced format (as displayed on Flipper screen)
    auth1, op1, _ = service.verify_tag_uid("2E B7 76")
    assert auth1 is True
    assert op1["operator_id"] == "OP-004"

    # Match via raw hex
    auth2, op2, _ = service.verify_tag_uid("2EB776")
    assert auth2 is True
    assert op2["operator_id"] == "OP-004"

    # Match via normalized
    auth3, op3, _ = service.verify_tag_uid("2E:B7:76")
    assert auth3 is True
    assert op3["operator_id"] == "OP-004"


def test_auth_service_authorized(test_db):
    """Verifies authorized operator test credential lookup."""
    service = AuthService(db_manager=test_db)
    authorized, operator, msg = service.verify_tag_uid("04:A2:3B:4C:5D:6E:7F")
    assert authorized is True
    assert operator is not None
    assert operator["operator_id"] == "OP-001"
    assert "Alice Vance" in msg


def test_auth_service_unauthorized(test_db):
    """Verifies unauthorized test credential rejection."""
    service = AuthService(db_manager=test_db)
    authorized, operator, msg = service.verify_tag_uid("DE:AD:BE:EF:00:11:22")
    assert authorized is False
    assert operator is None
    assert "Unauthorized" in msg


def test_auth_service_empty_uid(test_db):
    """Verifies empty UID handling."""
    service = AuthService(db_manager=test_db)
    authorized, operator, msg = service.verify_tag_uid("   ")
    assert authorized is False
    assert operator is None


def test_auth_api_endpoints():
    """Tests FastAPI /api/auth/scan and /api/operators endpoints."""
    with TestClient(app) as client:
        # 1. Test listing operators
        resp = client.get("/api/operators")
        assert resp.status_code == 200
        operators = resp.json()
        assert len(operators) >= 3

        # 2. Test valid scan
        scan_resp = client.post("/api/auth/scan", json={"tag_uid": "04:A2:3B:4C:5D:6E:7F"})
        assert scan_resp.status_code == 200
        data = scan_resp.json()
        assert data["authorized"] is True
        assert data["operator"]["operator_id"] == "OP-001"

        # 3. Test invalid scan
        bad_resp = client.post("/api/auth/scan", json={"tag_uid": "FF:EE:DD:CC:BB:AA"})
        assert bad_resp.status_code == 401
        bad_data = bad_resp.json()["detail"]
        assert bad_data["authorized"] is False


def test_flipper_zero_mission_lifecycle_and_voice_llm_gating(monkeypatch):
    """Proves Flipper Zero badge scan logs in, starts/finishes mission, and gates only voice/LLM."""
    import asyncio
    from backend.robot.interface import MotionCommand
    from backend.services.mission import mission_service

    monkeypatch.setenv("QUEST_API_KEY", "test-quest-key")
    with TestClient(app) as client:
        # Ensure clean mission state before test
        mission_service.stop_mission(reason="pre-test reset")
        gateway = app.state.gateway
        gateway._emergency_stopped = False

        # 1. Check Flipper status endpoint — no active mission initially
        flipper_status = client.get("/api/auth/flipper/status")
        assert flipper_status.status_code == 200
        assert flipper_status.json()["mission"]["active"] is False

        # 2. Without active mission:
        #    - WASD (/api/robot/command) succeeds
        #    - Hand joystick (gateway source="hand") succeeds
        #    - Voice (source="voice") and LLM (source="llm") are blocked with MISSION_REQUIRED
        wasd_resp = client.post("/api/robot/command", json={"action": "forward", "speed": 0.3})
        assert wasd_resp.status_code == 200

        hand_res = asyncio.run(gateway.motion(MotionCommand(forward=0.3), source="hand"))
        assert hand_res.status == "ACCEPTED"

        gateway._last_hand_at = 0.0
        voice_blocked = asyncio.run(gateway.motion(MotionCommand(forward=0.3), source="voice"))
        llm_blocked = asyncio.run(gateway.motion(MotionCommand(forward=0.3), source="llm"))
        assert voice_blocked.status == "MISSION_REQUIRED"
        assert llm_blocked.status == "MISSION_REQUIRED"

        # 3. Scan Flipper Zero badge 2E:B7:76 (OP-004) with mission_action="auto" -> logs in & starts mission
        scan1 = client.post(
            "/api/auth/scan",
            json={"tag_uid": "2E B7 76", "mission_action": "auto", "zone": "Sector-A"},
        )
        assert scan1.status_code == 200
        s1_data = scan1.json()
        assert s1_data["authorized"] is True
        assert s1_data["operator"]["operator_id"] == "OP-004"
        assert s1_data["mission_action_taken"] == "started"
        assert s1_data["mission"]["active"] is True
        assert s1_data["mission"]["status"] == "in_progress"

        # 4. With active mission: Voice and LLM now succeed!
        gateway._last_hand_at = 0.0
        voice_ok = asyncio.run(gateway.motion(MotionCommand(forward=0.3), source="voice"))
        llm_ok = asyncio.run(gateway.motion(MotionCommand(forward=0.3), source="llm"))
        assert voice_ok.status == "ACCEPTED"
        assert llm_ok.status == "ACCEPTED"

        # 5. Scan Flipper Zero badge 2E:B7:76 a second time -> finishes mission & re-locks Voice/LLM
        scan2 = client.post(
            "/api/auth/scan",
            json={"tag_uid": "2E:B7:76", "mission_action": "auto"},
        )
        assert scan2.status_code == 200
        s2_data = scan2.json()
        assert s2_data["authorized"] is True
        assert s2_data["mission_action_taken"] == "stopped"
        assert s2_data["mission"]["active"] is False
        assert s2_data["mission"]["status"] == "completed"

        gateway._last_hand_at = 0.0
        voice_relocked = asyncio.run(gateway.motion(MotionCommand(forward=0.3), source="voice"))
        llm_relocked = asyncio.run(gateway.motion(MotionCommand(forward=0.3), source="llm"))
        assert voice_relocked.status == "MISSION_REQUIRED"
        assert llm_relocked.status == "MISSION_REQUIRED"
        # Hand joystick still works even after mission finished
        hand_still_ok = asyncio.run(gateway.motion(MotionCommand(forward=0.3), source="hand"))
        assert hand_still_ok.status == "ACCEPTED"
