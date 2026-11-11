# BARRY Backend — Development & Testing Guide

## 1. Prerequisites & Environment Setup

```bash
cd <repo>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

---

## 2. Running the Backend Server

Start the FastAPI server with Uvicorn:

```bash
cd <repo>
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Swagger API Docs:** `http://localhost:8000/docs`
- **Web Command Cockpit:** `http://localhost:8000/cockpit`

> **Hardware Note (Flipper Zero):** When `uvicorn` is running, its built-in `FlipperListener` automatically connects to `/dev/serial/by-id/usb-Flipper_Devices_Inc.*` and engages the 125 kHz RFID reader. Do not run `scripts/flipper_serial.py` at the same time as `uvicorn`, as both would compete for `/dev/ttyACM0`.

---

## 3. Running Automated Tests

Run the full unit and integration test suite (`pytest`):

```bash
cd <repo>
.venv/bin/pytest tests/ -v
```

### Test Modules Overview
- `tests/test_auth.py` — UID normalization, HID H10301 (`2E:B7:76`) card verification, and `/api/auth/scan`.
- `tests/test_mission.py` & `tests/test_missions.py` — Mission lifecycle, `GET /api/missions`, `GET /api/missions/{mission_id}`, and `authorization_logs`.
- `tests/test_events.py` — Structured inspection event ingestion (`POST /api/events/ingest`), Pydantic validation, `event_uuid` idempotency, and missing mission handling.
- `tests/test_providers.py` — `MockProvider`, `OpenRouterProvider` (mocked HTTP success, 401 auth error, 429 retry, timeout, malformed JSON), `LocalLLMProvider` (DGX Spark), and `provider_factory.py`.
- `tests/test_ai.py` — `AIOrchestrator` analysis persistence in `ai_analyses`, primary-to-fallback failover, prompt-injection isolation (`<untrusted_observation>`), and `MissionReportGenerator`.
- `tests/test_api.py` — End-to-end AI REST endpoints and WebSocket `inspection_event` / `ai_alert` broadcasts.
- `tests/test_robot.py`, `tests/test_camera.py`, `tests/test_full_system.py` — Robot simulator, emergency stop lock, MJPEG camera streamer, and 8-step full mission pipeline.

---

## 4. Quick End-to-End Verification via `curl`

```bash
# 1. Authorize operator via NFC/RFID UID
curl -X POST http://localhost:8000/api/auth/scan \
  -H "Content-Type: application/json" \
  -d '{"tag_uid": "04:A2:3B:4C:5D:6E:7F"}'

# 2. Start a mission
curl -X POST http://localhost:8000/api/mission/start \
  -H "Content-Type: application/json" \
  -d '{"operator_id": "OP-001", "zone": "Zone-A"}'

# 3. Ingest a simulated DOGZILLA inspection event (replace MISSION_ID)
curl -X POST http://localhost:8000/api/events/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "mission_id": "MISSION_ID",
    "source": "dogzilla",
    "event_type": "object_detection",
    "data": {
      "object": "unauthorized_backpack",
      "confidence": 0.91,
      "zone": "Zone-A"
    }
  }'

# 4. Generate a post-inspection AI mission report
curl -X POST http://localhost:8000/api/ai/report/MISSION_ID
```
