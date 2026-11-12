"""Tests for mission management, MissionManager adapter, and authorization logs."""

import pytest
from fastapi.testclient import TestClient

from backend.auth.authorization import AuthService
from backend.database import DatabaseManager
from backend.main import app
from backend.missions.manager import MissionManager


@pytest.fixture
def isolated_db(tmp_path):
    db_file = str(tmp_path / "test_missions_v1.db")
    return DatabaseManager(db_path=db_file)


def test_mission_manager_list_and_get(isolated_db):
    mgr = MissionManager(db_manager=isolated_db)
    started = mgr.start_mission(operator_id="OP-001", zone="Reactor Bay 2")
    mission_id = started["mission_id"]

    fetched = mgr.get_mission_by_id(mission_id)
    assert fetched is not None
    assert fetched["mission_id"] == mission_id
    assert fetched["operator_id"] == "OP-001"
    assert fetched["status"] == "in_progress"

    all_missions = mgr.list_missions(limit=10)
    assert len(all_missions) >= 1
    assert any(m["mission_id"] == mission_id for m in all_missions)

    stopped = mgr.stop_mission(reason="Completed sector check")
    assert stopped["active"] is False

    fetched_after = mgr.get_mission_by_id(mission_id)
    assert fetched_after["status"] == "completed"
    assert fetched_after["ended_at"] is not None


def test_authorization_logs_recorded(monkeypatch, isolated_db):
    monkeypatch.setenv("AUTH_ALLOW_UNREGISTERED_TAGS", "false")
    auth = AuthService(db_manager=isolated_db)
    ok, op, _ = auth.verify_tag_uid("04:A2:3B:4C:5D:6E:7F")
    assert ok is True

    bad, _, _ = auth.verify_tag_uid("AA:BB:CC:DD")
    assert bad is False

    logs = auth.get_authorization_logs(limit=10)
    assert len(logs) == 2
    statuses = [entry["authorized"] for entry in logs]
    assert True in statuses
    assert False in statuses


def test_missions_rest_endpoints():
    with TestClient(app) as client:
        client.post("/api/mission/stop")
        start_resp = client.post(
            "/api/mission/start",
            json={"operator_id": "OP-001", "zone": "Substation North"},
        )
        assert start_resp.status_code == 200
        mission_id = start_resp.json()["mission_id"]

        list_resp = client.get("/api/missions")
        assert list_resp.status_code == 200
        missions = list_resp.json()
        assert any(m["mission_id"] == mission_id for m in missions)

        detail_resp = client.get(f"/api/missions/{mission_id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["mission_id"] == mission_id

        not_found_resp = client.get("/api/missions/MSN-DOES-NOT-EXIST")
        assert not_found_resp.status_code == 404

        client.post("/api/mission/stop")
