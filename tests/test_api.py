"""Integration tests for BARRY Intelligence Engine REST APIs and WebSocket broadcasts."""

from fastapi.testclient import TestClient
from backend.main import app


def test_ai_status_and_analyze_and_report_endpoints():
    with TestClient(app) as client:
        # 1. Check /api/ai/status
        status_resp = client.get("/api/ai/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert "provider" in status_data
        assert "model" in status_data
        assert "available" in status_data

        # 2. Start a mission
        client.post("/api/mission/stop")
        start_resp = client.post(
            "/api/mission/start",
            json={"operator_id": "OP-001", "zone": "Cryo Lab 3"},
        )
        assert start_resp.status_code == 200
        mission_id = start_resp.json()["mission_id"]

        # 3. Direct ad-hoc analysis via POST /api/ai/analyze
        analyze_resp = client.post(
            "/api/ai/analyze",
            json={
                "mission_id": mission_id,
                "source": "dogzilla",
                "event_type": "overheat",
                "data": {
                    "object": "overheat",
                    "zone": "Cryo Lab 3",
                    "confidence": 0.87,
                    "psi": 185,
                },
            },
        )
        assert analyze_resp.status_code == 200
        analysis = analyze_resp.json()
        assert analysis["mission_id"] == mission_id
        assert analysis["risk_level"] in ("medium", "high")
        assert analysis["analysis"]["recommended_action"]

        # 4. Ingest an event with auto_analyze=False, then analyze by event_id
        ingest_resp = client.post(
            "/api/events/ingest",
            json={
                "mission_id": mission_id,
                "source": "dogzilla",
                "event_type": "obstacle",
                "data": {"object": "obstacle", "zone": "Cryo Lab 3", "confidence": 0.82},
                "auto_analyze": False,
            },
        )
        assert ingest_resp.status_code == 200
        event_id = ingest_resp.json()["id"]
        assert ingest_resp.json()["ai_analysis"] is None

        analyze_by_id_resp = client.post(
            "/api/ai/analyze",
            json={"event_id": event_id},
        )
        assert analyze_by_id_resp.status_code == 200
        assert analyze_by_id_resp.json()["event_id"] == event_id

        # 5. List analyses for this mission
        analyses_resp = client.get(f"/api/ai/analyses?mission_id={mission_id}")
        assert analyses_resp.status_code == 200
        assert len(analyses_resp.json()) >= 2

        # 6. Stop mission and generate AI mission report
        client.post("/api/mission/stop", json={"reason": "Cryo Lab inspection finished"})
        report_resp = client.post(f"/api/ai/report/{mission_id}")
        assert report_resp.status_code == 200
        report = report_resp.json()
        assert report["mission_id"] == mission_id
        assert "# BARRY Inspection Report" in report["report_markdown"]

        # 7. Non-existent mission report returns 404
        bad_report = client.post("/api/ai/report/MSN-NOT-FOUND")
        assert bad_report.status_code == 404


def test_websocket_ai_alert_broadcast():
    with TestClient(app) as client:
        client.post("/api/mission/stop")
        start_resp = client.post(
            "/api/mission/start",
            json={"operator_id": "OP-001", "zone": "Containment Wall East"},
        )
        assert start_resp.status_code == 200
        mission_id = start_resp.json()["mission_id"]

        with client.websocket_connect("/ws") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "welcome"

            # Trigger an event ingestion and verify WS broadcasts inspection_event and ai_alert
            resp = client.post(
                "/api/events/ingest",
                json={
                    "mission_id": mission_id,
                    "source": "dogzilla",
                    "event_type": "gas_leak",
                    "data": {
                        "object": "gas_leak",
                        "zone": "Containment Wall East",
                        "confidence": 0.95,
                    },
                },
            )
            assert resp.status_code == 200

            received_types = set()
            for _ in range(5):
                msg = ws.receive_json()
                received_types.add(msg["type"])
                if "inspection_event" in received_types and "ai_alert" in received_types:
                    break
            assert "inspection_event" in received_types
            assert "ai_alert" in received_types

        client.post("/api/mission/stop")
