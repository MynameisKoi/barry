"""Tests for structured inspection event ingestion, validation, idempotency, and retrieval."""

import uuid
import pytest
from fastapi.testclient import TestClient

from backend.database import DatabaseManager
from backend.events.logger import EventLogger
from backend.main import app
from backend.services.mission import MissionService


@pytest.fixture
def isolated_db(tmp_path):
    db_file = str(tmp_path / "test_events_v1.db")
    return DatabaseManager(db_path=db_file)


def test_event_logger_ingest_and_idempotency(isolated_db):
    mission_svc = MissionService(db_manager=isolated_db)
    started = mission_svc.start_mission(operator_id="OP-001", zone="Turbine Hall B")
    mission_id = started["mission_id"]

    logger = EventLogger(db_manager=isolated_db)
    dedup_key = f"evt-{uuid.uuid4().hex[:10]}"

    first = logger.ingest_event(
        mission_id=mission_id,
        source="dogzilla",
        event_type="thermal_anomaly",
        data={"temp_c": 88.4, "component": "bearing-3", "zone": "Turbine Hall B"},
        event_uuid=dedup_key,
    )
    assert first["event_type"] == "thermal_anomaly"
    assert first["mission_id"] == mission_id

    # Second call with identical event_uuid must be idempotent and return the same id
    second = logger.ingest_event(
        mission_id=mission_id,
        source="dogzilla",
        event_type="thermal_anomaly",
        data={"temp_c": 88.4, "component": "bearing-3", "zone": "Turbine Hall B"},
        event_uuid=dedup_key,
    )
    assert second["id"] == first["id"]

    fetched = logger.get_inspection_event(first["id"])
    assert fetched is not None
    assert fetched["data"]["temp_c"] == 88.4


def test_events_ingest_api_validation_and_missing_mission():
    with TestClient(app) as client:
        # 1. Missing mission_id returns 404
        missing_resp = client.post(
            "/api/events/ingest",
            json={
                "mission_id": "MSN-NONEXISTENT-999",
                "source": "dogzilla",
                "event_type": "obstacle_detected",
                "data": {"distance_m": 0.35, "zone": "Corridor 4"},
            },
        )
        assert missing_resp.status_code == 404

        # 2. Invalid payload (empty event_type) rejected by Pydantic with 422
        invalid_resp = client.post(
            "/api/events/ingest",
            json={
                "mission_id": "MSN-TEST",
                "source": "dogzilla",
                "event_type": "",
                "data": {},
            },
        )
        assert invalid_resp.status_code == 422

        # 3. Start a valid mission and ingest a structured inspection event
        client.post("/api/mission/stop")
        start_resp = client.post(
            "/api/mission/start",
            json={"operator_id": "OP-001", "zone": "Chemical Storage Vault"},
        )
        assert start_resp.status_code == 200
        mission_id = start_resp.json()["mission_id"]

        dedup_uuid = f"api-evt-{uuid.uuid4().hex[:10]}"
        valid_resp = client.post(
            "/api/events/ingest",
            json={
                "event_uuid": dedup_uuid,
                "mission_id": mission_id,
                "source": "dogzilla",
                "event_type": "gas_leak",
                "data": {
                    "object": "gas_leak",
                    "gas": "H2S",
                    "ppm": 45,
                    "zone": "Chemical Storage Vault",
                    "confidence": 0.96,
                },
            },
        )
        assert valid_resp.status_code == 200
        data = valid_resp.json()
        assert data["ai_analysis"] is not None
        assert data["ai_analysis"]["risk_level"] == "high"
        event_id = data["id"]

        # Duplicate submission returns same id and preserves existing analysis
        dup_resp = client.post(
            "/api/events/ingest",
            json={
                "event_uuid": dedup_uuid,
                "mission_id": mission_id,
                "source": "dogzilla",
                "event_type": "gas_leak",
                "data": {
                    "object": "gas_leak",
                    "gas": "H2S",
                    "ppm": 45,
                    "zone": "Chemical Storage Vault",
                    "confidence": 0.96,
                },
            },
        )
        assert dup_resp.status_code == 200
        assert dup_resp.json()["id"] == event_id

        # Retrieve single event via GET /api/events/{event_id}
        get_resp = client.get(f"/api/events/{event_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == event_id
        assert get_resp.json()["ai_analysis"] is not None

        client.post("/api/mission/stop")
