# BARRY API Specification

The BARRY backend provides RESTful endpoints for configuration, mission state, and discrete commands, alongside a high-throughput WebSocket channel (`/ws`) for real-time telemetry and alerts.

---

## Base URLs
- **REST**: `http://<LAPTOP_LAN_IP>:8000`
- **WebSocket**: `ws://<LAPTOP_LAN_IP>:8000/ws`
- **Swagger Documentation**: `http://<LAPTOP_LAN_IP>:8000/docs`

---

## REST Endpoints

### 1. Authentication & NFC
#### `POST /api/auth/scan`
Verify an NFC test credential scanned by Flipper Zero or entered manually.
- **Request Body**:
  ```json
  {
    "tag_uid": "04:A2:3B:4C:5D:6E:7F"
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "authorized": true,
    "operator": {
      "operator_id": "OP-001",
      "name": "Alice Vance",
      "role": "Chief Inspection Lead",
      "tag_uid": "04:A2:3B:4C:5D:6E:7F"
    },
    "message": "Authorized: Alice Vance (Chief Inspection Lead)"
  }
  ```
- **Response (401 Unauthorized)**:
  ```json
  {
    "detail": {
      "authorized": false,
      "operator": null,
      "message": "Unauthorized credential: UID FF:EE:DD:CC:BB:AA is not registered."
    }
  }
  ```

#### `GET /api/operators`
Returns list of pre-configured authorized operators and test tag UIDs.

---

### 2. Missions
#### `POST /api/mission/start`
Start an authorized inspection mission.
- **Request Body**:
  ```json
  {
    "operator_id": "OP-001",
    "zone": "Sector-North"
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "active": true,
    "mission_id": "MSN-A8B9C0D1",
    "operator_id": "OP-001",
    "operator_name": "Alice Vance",
    "zone": "Sector-North",
    "status": "in_progress",
    "started_at": "2026-11-02T16:00:00Z",
    "ended_at": null
  }
  ```

#### `POST /api/mission/stop`
Concludes the currently active inspection mission.
- **Request Body**:
  ```json
  {
    "reason": "Inspection sweep complete"
  }
  ```

#### `GET /api/mission/status`
Returns information on the active mission or status of the last mission.

---

### 3. Robot Locomotion
#### `GET /api/robot/status`
Returns connection, telemetry coordinates, heading, and battery level.
- **Response (200 OK)**:
  ```json
  {
    "connected": true,
    "mode": "simulation",
    "battery_level": 98.4,
    "position": {
      "x": 1.2,
      "y": 0.5,
      "heading": 15.0
    },
    "speed": 0.2,
    "last_command": "forward",
    "emergency_stopped": false,
    "timestamp": "2026-11-02T16:05:00Z"
  }
  ```

#### `POST /api/robot/command`
Send movement commands to the robot.
- **Actions**: `forward`, `backward`, `left`, `right`, `stop`, `emergency_stop`
- **Request Body**:
  ```json
  {
    "action": "forward",
    "speed": 0.2
  }
  ```

#### `POST /api/robot/reset-emergency`
Clears emergency stop lock to resume locomotion.

---

### 4. Audit Trail & Events
#### `GET /api/events?mission_id=MSN-...&limit=50`
Retrieve chronological log of auth events, commands, alerts, and stops.

---

### 5. AI Inspection Alerts
#### `POST /api/ai/trigger-detection?zone=Zone-A`
Triggers an object detection alert (simulated/live) and broadcasts to VR headset.

---

### 6. Camera Streaming & Video Feeds
#### `GET /api/camera/stream`
Real-time MJPEG video stream (multipart/x-mixed-replace) consumed directly by Meta Quest 3S or web clients.

#### `GET /api/camera/snapshot`
Returns the latest single frame as JPEG binary (`image/jpeg`).

---

## Flipper Zero USB Integration
When Flipper Zero is plugged in via USB-C, the backend automatically monitors serial lines (`/dev/ttyACM*`) at 115200 baud for NFC scans.
Upon detection, the UID is normalized, authorized, and pushed over WebSocket to the VR headset in real-time.

---

## WebSocket Channel (`/ws`)

### Sent by Server to VR Headset
1. **Periodic Telemetry (every 1s)**:
   ```json
   {
     "type": "telemetry",
     "telemetry": { ... },
     "mission": { ... }
   }
   ```
2. **AI Detection Alert**:
   ```json
   {
     "type": "detection",
     "object": "person",
     "confidence": 0.94,
     "zone": "Restricted Area",
     "timestamp": "2026-11-02T16:00:00Z",
     "bounding_box": [0.15, 0.20, 0.55, 0.60]
   }
   ```
3. **Emergency Stop Event**:
   ```json
   {
     "type": "emergency_stop",
     "message": "EMERGENCY STOP ACTIVATED! Robot immobilized."
   }
   ```

### Received from VR Headset
The VR operator can issue commands directly over WebSocket:
```json
{
  "type": "robot_command",
  "action": "forward",
  "speed": 0.2
}
```
Or trigger emergency stop:
```json
{
  "type": "emergency_stop"
}
```
