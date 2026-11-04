# BARRY: AI-Powered Immersive Robotic Inspection System

**BARRY** is an AI-powered robotic inspection system engineered for high-risk and restricted industrial environments. It fuses physical NFC credential authorization with real-time quadruped robot locomotion and an immersive VR operator command center.

---

## 🚀 Hardware & Architecture Overview

- **Flipper Zero**: NFC-based physical mission credential authorization demonstration.
- **Meta Quest 3S**: Immersive operator command center with real-time video feed, telemetry, and locomotion controls.
- **DOGZILLA S1/S2 Quadruped**: Physical quadruped robotic dog for agile physical inspection and sensor gathering.
- **Central Laptop Processing Gateway**: FastAPI backend, SQLite audit log database, WebSocket hub, and YOLO vision inference.

```
META QUEST 3S  <---(WebSocket / HTTP)--->  LAPTOP GATEWAY  <---(Serial/ROS2)--->  DOGZILLA S1/S2
                                                   ^
                                                   |---(NFC Auth)--- FLIPPER ZERO
```

---

## 📁 Repository Structure

```
barry/
├── backend/
│   ├── auth/
│   │   ├── authorization.py    # Flipper Zero NFC verification & operator registry
│   │   └── flipper_listener.py # USB serial auto-listener for Flipper Zero (/dev/ttyACM*)
│   ├── camera/
│   │   └── streamer.py         # MJPEG live stream & synthetic quadruped HUD fallback
│   ├── robot/
│   │   ├── interface.py        # Abstract RobotInterface decoupling backend from hardware
│   │   ├── simulator.py        # Full kinematics & telemetry simulator (Day 1-3 dev)
│   │   └── dogzilla.py         # Hardware driver placeholder ready for physical DOGZILLA
│   ├── ai/
│   │   └── detection.py        # Lightweight YOLO / vision alert generator & contour tracker
│   ├── services/
│   │   └── mission.py          # Mission lifecycle, zone tracking & audit trail
│   ├── database.py             # SQLite manager & schema migrations
│   ├── models.py               # Pydantic schemas for REST & WebSocket
│   └── main.py                 # FastAPI application, camera streaming & WebSocket broadcaster
├── frontend/
│   └── unity-project/
│       └── Assets/Scripts/     # C# Unity VR Cockpit controllers:
│           ├── BarryNetworkManager.cs  # WebSocket & REST network bridge
│           ├── BarryCameraReceiver.cs  # MJPEG & snapshot texture renderer
│           ├── BarryCockpitUI.cs       # 5 HUD panels, AI alerts, emergency stop
│           └── BarryInputController.cs # Keyboard WASD & Meta XR touch controller inputs
├── tools/
│   ├── register_tag.py         # Interactive CLI to enroll physical NFC tags
│   └── flipper_serial.py       # Standalone USB serial monitor for Flipper Zero
├── tests/
│   ├── test_auth.py            # NFC authorization & UID normalization unit tests
│   ├── test_camera.py          # MJPEG streaming & AI vision detection tests
│   ├── test_robot.py           # Locomotion & emergency stop unit tests
│   ├── test_mission.py         # Mission lifecycle & audit trail unit tests
│   └── test_full_system.py     # End-to-end 8-stage mission verification harness
├── docs/
│   ├── architecture.md         # Detailed topology & architectural principles
│   ├── api-specification.md    # REST & WebSocket endpoint contracts
│   ├── quest3s-setup-guide.md  # Meta Quest 3S developer mode, ADB & Wi-Fi guide
│   └── setup-guide.md          # Step-by-step developer installation guide
├── requirements.txt            # Python dependencies
├── .env.example                # Sample environment variables
└── README.md                   # Project overview & quickstart
```

---

## ⚡ Quickstart

### 1. Set Up Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Tests
```bash
pytest -v
```

### 3. Start Backend Server
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive API documentation will be available at:
- `http://localhost:8000/docs`

---

## 🧪 Testing the APIs via Terminal

### A. Authorize Mission via NFC Credential
```bash
# Valid demo NFC tag UID (Alice Vance)
curl -X POST http://localhost:8000/api/auth/scan \
  -H "Content-Type: application/json" \
  -d '{"tag_uid": "04:A2:3B:4C:5D:6E:7F"}'

# Unauthorized tag UID test
curl -X POST http://localhost:8000/api/auth/scan \
  -H "Content-Type: application/json" \
  -d '{"tag_uid": "FF:EE:DD:CC:BB:AA"}'
```

### B. Start an Inspection Mission
```bash
curl -X POST http://localhost:8000/api/mission/start \
  -H "Content-Type: application/json" \
  -d '{"operator_id": "OP-001", "zone": "Zone-A"}'
```

### C. Issue Locomotion Commands to Robot Simulator
```bash
# Move forward
curl -X POST http://localhost:8000/api/robot/command \
  -H "Content-Type: application/json" \
  -d '{"action": "forward", "speed": 0.3}'

# Query current robot coordinates & battery status
curl -X GET http://localhost:8000/api/robot/status

# Trigger Emergency Stop
curl -X POST http://localhost:8000/api/robot/command \
  -H "Content-Type: application/json" \
  -d '{"action": "emergency_stop", "speed": 0.0}'

# Reset Emergency Stop
curl -X POST http://localhost:8000/api/robot/reset-emergency
```

### D. Video Streaming & Snapshots
```bash
# View live MJPEG stream URL (open in browser or Unity):
http://localhost:8000/api/camera/stream

# Grab instantaneous snapshot:
curl -s http://localhost:8000/api/camera/snapshot -o snapshot.jpg
```

### E. Trigger AI Object Detection Alert
```bash
curl -X POST "http://localhost:8000/api/ai/trigger-detection?zone=Zone-A"
```

### F. Stop Mission & Review Event History
```bash
# Stop active mission
curl -X POST http://localhost:8000/api/mission/stop \
  -H "Content-Type: application/json" \
  -d '{"reason": "Sweep completed successfully"}'

# Retrieve chronological audit history
curl -X GET http://localhost:8000/api/events
```

### G. Flipper Zero Tools
```bash
# 1. Enroll physical NFC test tags into the database:
python3 tools/register_tag.py --id OP-004 --name "Lead Inspector" --role "Safety Officer" --uid "04:A2:3B:4C"

# 2. List enrolled credentials:
python3 tools/register_tag.py --list

# 3. Standalone Flipper Zero USB serial monitor:
python3 tools/flipper_serial.py
```

---

## 👥 Team Responsibilities & Workflow
- **Person A (Backend, Robotics, AI, Flipper)**:
  - Manages FastAPI backend, SQLite database, and WebSocket stream.
  - Maintains `RobotSimulator` and integrates `DogzillaController` when DOGZILLA arrives.
  - Implements Flipper Zero NFC UID receiver and YOLO detection pipeline.
- **Person B (VR & Meta Quest 3S)**:
  - Builds the immersive inspection cockpit in Unity 6 using the Meta XR SDK.
  - Subscribes to the `/ws` WebSocket channel for telemetry and AI detection alerts.
  - Maps Quest 3S touch controllers to `/api/robot/command`.
