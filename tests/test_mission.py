"""Tests for Mission Management service, event audit log, and API endpoints."""

import pytest
from fastapi.testclient import TestClient
from backend.database import DatabaseManager
from backend.main import app
from backend.services.mission import MissionService


@pytest.fixture
def test_db(tmp_path):
    """Creates an isolated temporary database for test execution."""
    db_file = str(tmp_path / "test_mission.db")
    mgr = DatabaseManager(db_path=db_file)
    return mgr


def test_mission_lifecycle(test_db):
    """Verifies starting, querying status, and stopping a mission."""
    service = MissionService(db_manager=test_db)

    # 1. Start mission with valid operator
    mission = service.start_mission(operator_id="OP-001", zone="Sector-North")
    assert mission["active"] is True
    assert mission["status"] == "in_progress"
    assert mission["operator_name"] == "Alice Vance"
    mission_id = mission["mission_id"]

    # 2. Query status
    status = service.get_status()
    assert status["active"] is True
    assert status["mission_id"] == mission_id

    # 3. Starting another concurrent mission should raise error
    with pytest.raises(RuntimeError):
        service.start_mission(operator_id="OP-002", zone="Sector-South")

    # 4. Stop mission
    stop_result = service.stop_mission(reason="Area secure")
    assert stop_result["active"] is False
    assert stop_result["status"] == "completed"

    # 5. Query status after completion
    post_status = service.get_status()
    assert post_status["active"] is False


def test_invalid_operator_mission_start(test_db):
    """Verifies that non-existent operators cannot start a mission."""
    service = MissionService(db_manager=test_db)
    with pytest.raises(ValueError):
        service.start_mission(operator_id="NON_EXISTENT_OP", zone="Zone-X")


def test_mission_events_logging(test_db):
    """Verifies event logging and retrieval."""
    service = MissionService(db_manager=test_db)
    m = service.start_mission(operator_id="OP-001", zone="Zone-A")

    service.log_event("detection", "Simulated anomaly detected", mission_id=m["mission_id"])
    events = service.get_events(mission_id=m["mission_id"])

    assert len(events) >= 2  # mission_start + detection
    event_types = [e["event_type"] for e in events]
    assert "mission_start" in event_types
    assert "detection" in event_types


def test_mission_api_endpoints():
    """Tests FastAPI /api/mission/start, /status, /stop, and /api/events endpoints."""
    with TestClient(app) as client:
        # 1. Stop any currently active mission from previous tests
        client.post("/api/mission/stop")

        # 2. Start mission
        start_resp = client.post("/api/mission/start", json={"operator_id": "OP-001", "zone": "Zone-A"})
        assert start_resp.status_code == 200
        start_data = start_resp.json()
        assert start_data["active"] is True
        assert start_data["status"] == "in_progress"

        # 3. Check status
        status_resp = client.get("/api/mission/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["active"] is True

        # 4. Trigger AI detection alert
        ai_resp = client.post("/api/ai/trigger-detection?zone=Zone-A")
        assert ai_resp.status_code == 200
        assert ai_resp.json()["type"] == "detection"

        # 5. Stop mission
        stop_resp = client.post("/api/mission/stop", json={"reason": "Inspection completed successfully"})
        assert stop_resp.status_code == 200
        assert stop_resp.json()["active"] is False

        # 6. Retrieve events
        events_resp = client.get("/api/events")
        assert events_resp.status_code == 200
        assert len(events_resp.json()) > 0
