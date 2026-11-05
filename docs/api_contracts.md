# BARRY Backend v0.1 — API Contracts & WebSocket Specification

This document defines the REST API and WebSocket contracts between the **BARRY Intelligence Engine Backend**, the **Meta Quest 3S Unity VR Command Center**, the **DOGZILLA S1/S2 Robot Controller**, and the **Flipper Zero NFC/RFID Reader**.

---

## 1. Base URLs

- **REST API Base URL:** `http://<BACKEND_HOST>:8000`
- **WebSocket Endpoint:** `ws://<BACKEND_HOST>:8000/ws`
- **Interactive Swagger UI:** `http://<BACKEND_HOST>:8000/docs`
- **Web Command Cockpit:** `http://<BACKEND_HOST>:8000/cockpit`

---

## 2. Authentication & NFC Credential Endpoints

### `POST /api/auth/scan`
Verifies an NFC/RFID credential UID scanned via Flipper Zero or entered manually, and records the attempt in `authorization_logs`.

**Request Body:**
```json
{
  "tag_uid": "2E:B7:76"
}
```

**Response (200 OK):**
```json
{
  "authorized": true,
  "operator": {
    "operator_id": "OP-004",
    "name": "Primary Field Lead",
    "role": "Robotic Mission Commander",
    "tag_uid": "2E:B7:76"
  },
  "message": "Authorized: Primary Field Lead (Robotic Mission Commander)"
}
```

### `GET /api/operators`
Returns all registered test operators and their demo NFC/RFID UIDs.

### `GET /api/auth/logs?limit=50`
Returns audit entries from `authorization_logs`.

---

## 3. Mission Management Endpoints

### `POST /api/mission/start`
Starts a new inspection mission for an authorized operator.

**Request Body:**
```json
{
  "operator_id": "OP-001",
  "zone": "Zone-A"
}
```

**Response (200 OK):**
```json
{
  "active": true,
  "mission_id": "MSN-8A41F09C",
  "operator_id": "OP-001",
  "operator_name": "Alice Vance",
  "zone": "Zone-A",
  "status": "in_progress",
  "started_at": "2026-11-05T12:00:00+00:00",
  "ended_at": null
}
```

### `POST /api/mission/stop`
Stops the currently active mission and halts robot movement.

**Request Body:**
```json
{
  "reason": "Inspection sweep completed"
}
```

### `GET /api/mission/status`
Returns the current active mission status (or most recent mission if idle).

### `GET /api/missions?limit=50`
Returns a list of recent and historical missions.

### `GET /api/missions/{mission_id}`
Returns complete mission details, including `inspection_events`, `ai_analyses`, and `audit_events`.

---

## 4. Structured Inspection Event Ingestion Endpoints

### `POST /api/events/ingest`
Ingests a structured observation from DOGZILLA or the simulator into `inspection_events`, optionally runs AI analysis, and broadcasts updates over WebSocket. Supports idempotency via `event_uuid`.

**Request Body:**
```json
{
  "event_uuid": "evt-optional-dedup-id-001",
  "mission_id": "MSN-8A41F09C",
  "source": "dogzilla",
  "event_type": "object_detection",
  "data": {
    "object": "unauthorized_backpack",
    "confidence": 0.91,
    "zone": "Zone-A",
    "telemetry": {
      "battery": 78,
      "mode": "patrol",
      "obstacle_distance_cm": 54
    }
  },
  "auto_analyze": true
}
```

**Response (200 OK):**
```json
{
  "id": 1,
  "event_uuid": "evt-optional-dedup-id-001",
  "mission_id": "MSN-8A41F09C",
  "source": "dogzilla",
  "event_type": "object_detection",
  "data": {
    "object": "unauthorized_backpack",
    "confidence": 0.91,
    "zone": "Zone-A"
  },
  "timestamp": "2026-11-05T12:01:00+00:00",
  "ai_analysis": {
    "id": 1,
    "mission_id": "MSN-8A41F09C",
    "event_id": 1,
    "provider": "mock",
    "model": "barry-mock-v1",
    "risk_level": "medium",
    "analysis": {
      "risk_level": "medium",
      "summary": "A unauthorized_backpack was detected in Zone-A.",
      "recommended_action": "Request operator verification via VR camera feed before continuing patrol.",
      "requires_human_review": true,
      "detected_objects": ["unauthorized_backpack"],
      "reasoning_summary": "Observed fact: Sensor reported 'unauthorized_backpack' in Zone-A (confidence: 0.91). Interpretation: The detection indicates presence in the inspection area, but available sensor data alone does not establish whether it is authorized."
    },
    "created_at": "2026-11-05T12:01:00+00:00",
    "status": "completed",
    "latency_ms": 0.42,
    "error_message": null
  }
}
```

### `GET /api/events/{event_id}`
Retrieves a single structured inspection event and its associated `ai_analysis`.

### `GET /api/events/structured?mission_id={mission_id}&limit=50`
Lists structured inspection events stored in `inspection_events`.

### `GET /api/events?mission_id={mission_id}&limit=50`
Lists unified system audit trail events.

---

## 5. AI Intelligence Engine Endpoints

### `POST /api/ai/analyze`
Triggers AI analysis on either an existing `event_id` or an ad-hoc observation payload.

**Request Body:**
```json
{
  "event_id": 1
}
```
*(Or pass `mission_id`, `source`, `event_type`, and `data` directly.)*

### `POST /api/ai/report/{mission_id}`
Generates a post-inspection `MissionReport` separating recorded factual observations from AI assessment and recommendations, including a complete Markdown report (`report_markdown`).

### `GET /api/ai/status`
Returns the health and readiness status of the configured LLM provider and fallback provider.

### `GET /api/ai/analyses?mission_id={mission_id}&event_id={event_id}`
Lists stored AI analysis records from `ai_analyses`.

---

## 6. Robot Locomotion & Camera Endpoints

- `GET /api/robot/status` — Returns battery, coordinates, mode, and emergency stop state.
- `POST /api/robot/command` — Sends movement command (`forward`, `backward`, `left`, `right`, `stop`, `emergency_stop`).
- `POST /api/robot/reset-emergency` — Clears emergency stop lock.
- `GET /api/camera/stream` — Multipart MJPEG video stream for VR headset.
- `GET /api/camera/snapshot` — Single JPEG frame snapshot.

---

## 7. WebSocket Notifications (`ws://<HOST>:8000/ws`)

The backend broadcasts JSON messages with a top-level `type` field:

- `welcome` — Initial connection handshake with current telemetry and mission state.
- `telemetry` — Periodic 1 Hz robot status and mission heartbeat.
- `auth_event` — Broadcast immediately when a Flipper Zero USB scan or `/api/auth/scan` occurs.
- `mission_update` — Broadcast when a mission starts or stops.
- `inspection_event` — Broadcast when a new structured observation is ingested.
- `ai_alert` — Broadcast when BARRY AI analysis completes:
  ```json
  {
    "type": "ai_alert",
    "mission_id": "MSN-8A41F09C",
    "event_id": 1,
    "risk_level": "medium",
    "summary": "A unauthorized_backpack was detected in Zone-A.",
    "recommended_action": "Request operator verification via VR camera feed before continuing patrol.",
    "requires_human_review": true,
    "detected_objects": ["unauthorized_backpack"],
    "provider": "mock",
    "model": "barry-mock-v1",
    "timestamp": "2026-11-05T12:01:00+00:00"
  }
  ```
- `mission_report` — Broadcast when a mission inspection report is generated.
