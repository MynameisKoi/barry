# BARRY System Architecture

## Overview
**BARRY** is an AI-powered immersive robotic inspection platform uniting three core hardware platforms:
1. **Flipper Zero**: Physical mission credential demonstration via NFC tag reading.
2. **Meta Quest 3S**: Immersive VR operator command center for viewing camera feeds, telemetry, and issuing locomotion controls.
3. **DOGZILLA S1/S2**: Physical quadruped robotic dog carrying onboard sensors for inspection missions. Its onboard Raspberry Pi hosts a small robot-control API.

The system runs as two services:

| Service | Runs on | Entry point | Responsibility |
|---|---|---|---|
| **Main backend** | Operator laptop | `backend.main:app` (port 8000) | Quest REST + WebSocket, Flipper NFC auth, missions, audit DB, AI detection, camera stream |
| **Robot API** | DOGZILLA's Raspberry Pi | `backend.robot.server:app` (port 8100) | Robot control only: locomotion, stop, emergency stop, telemetry |

---

## Topology Diagram

```
+-------------------------------------------------------------+
|                      META QUEST 3S                          |
|         (VR Command Center / Headset Display)               |
+-------------------------------------------------------------+
                               |
             WebSocket / HTTP  | Wi-Fi LAN  (port 8000)
                               v
+-------------------------------------------------------------+
|               PRIMARY LAPTOP GATEWAY & BACKEND              |
|                                                             |
|  +-------------------------------------------------------+  |
|  |                     FastAPI API                       |  |
|  |  * REST Endpoints (/api/auth, /api/mission, /api/robot) |
|  |  * Real-Time WebSocket (/ws)                          |  |
|  +-------------------------------------------------------+  |
|                               |                             |
|  +----------------------------+--------------------------+  |
|  |             |              |             |            |  |
|  v             v              v             v            v  |
| [Auth]     [Mission]      [Locomotion]     [AI]       [DB]  |
| Flipper    Lifecycle &    RobotInterface  YOLO &    SQLite  |
| NFC Tags   Audit Events   (Remote client) Vision    Events  |
+-------------------------------------------------------------+
               |                               |
               | USB / Wi-Fi                   | HTTP + X-Robot-Key
               |                               | Wi-Fi LAN (port 8100)
               v                               v
+-----------------------------+ +-----------------------------+
|        FLIPPER ZERO         | |  DOGZILLA S1/S2 ROBOT       |
|   NFC Test Card Reader      | |  +-----------------------+  |
+-----------------------------+ |  | Raspberry Pi          |  |
                                |  | Robot API (FastAPI)   |  |
                                |  | * Watchdog auto-stop  |  |
                                |  | * DogzillaController  |  |
                                |  +-----------+-----------+  |
                                |              | Serial       |
                                |              v              |
                                |  Motion control board       |
                                +-----------------------------+
```

---

## Architectural Principles

1. **Hardware Decoupling via RobotInterface**:
   The main backend talks to the robot only through the abstract `RobotInterface` (`backend/robot/interface.py`). `ROBOT_MODE` selects the implementation:

   | `ROBOT_MODE` | Implementation | Used where |
   |---|---|---|
   | `simulation` | `RobotSimulator` | Laptop, for development and tests without hardware |
   | `remote` | `RemoteRobotClient` | Laptop in production; forwards calls to the Pi Robot API over HTTP |
   | `dogzilla` | `DogzillaController` | Pi only; drives the DOGZILLA board over serial |

   Because every implementation honours the same contract, switching from the simulator to the real robot changes neither the Unity app nor the laptop's `/api/*` and `/ws` contracts.

2. **Laptop as Central Processing Unit**:
   Heavy tasks (YOLO object detection inference, database persistence, mission state, and WebSocket broadcasting) run on the operator laptop to preserve battery and compute on the robotic platform. The Pi runs the Robot API and shared camera capture service with `opencv-python-headless`; ONNX Runtime remains laptop-side.

3. **Single Owner of the Hardware**:
   Only the Pi talks to the DOGZILLA serial port. The Robot API runs as one process (`uvicorn --workers 1`, managed by systemd) and serializes serial writes with a lock, so emergency stop state lives in exactly one place.

4. **Same Subnet Local Area Network**:
   The Quest, laptop, and Pi share the local Wi-Fi.
   - Quest → laptop: `http://<LAPTOP_IP>:8000`, `ws://<LAPTOP_IP>:8000/ws`
   - Laptop → Pi: `ROBOT_REMOTE_URL`, e.g. `http://barry-pi.local:8100` (reserve a fixed DHCP address for the Pi)

   In normal operation the Quest never connects to the Pi directly. The one exception is **emergency mode**, a fallback for when the laptop is unavailable: the Quest sends hand commands straight to the Pi and shows the raw camera feed straight from the Pi (no detection, no LLM) (see Phase 4 of [control-plan.md](control-plan.md)).

5. **Fail-Safe & Safety Architecture**:
   - `emergency_stop` cuts locomotion immediately and sets a latch on the Pi that blocks movement until `/api/v1/emergency-stop/reset` is called. Emergency stop is never gated by missions or auth state on the laptop.
   - **Watchdog**: the Pi stops the robot if no command arrives within `ROBOT_WATCHDOG_MS` (default 500 ms). Clients resend movement commands while a direction is held, so a dropped laptop or Wi-Fi link brings the robot to a halt.
   - **Speed cap**: the Pi limits requested speed to `ROBOT_MAX_SPEED`.
   - **Link loss**: if the Pi is unreachable, `RemoteRobotClient` reports `connected: false` instead of raising, so the Quest shows the robot as offline.
   - Movements are intended to require active mission authorization (enforced on the laptop; planned, not yet implemented in `backend/main.py`).

6. **Security Boundary**:
   Every Robot API route except `/healthz` requires the `X-Robot-Key` header to match `ROBOT_API_KEY`, a shared secret configured on both the laptop and the Pi. In `dogzilla` mode the Pi app fails on its first request if the key is unset (`ROBOT_API_KEY is required`), and the Robot API does not enable CORS because only the laptop backend calls it.

---

## Robot API (Pi and laptop)

The Pi serves the v1 Robot API on port 8100 (`backend/robot/server.py`). The Quest does not call it directly. It calls the laptop's `/api/v1/*` relay, which forwards to the Pi. The current routes are in [api-specification.md](api-specification.md).

### Legacy: web cockpit, to be retired in Phase 2 cleanup

The table below lists the older `/api/robot/*` routes on the laptop (`backend/robot/router.py`, mounted in `backend/main.py`). The web cockpit still uses them. The Quest must use `/api/v1/*` instead. These routes will be removed once the cockpit moves to `/api/v1/*`.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/healthz` | No | Liveness check for systemd and monitoring |
| GET | `/api/robot/status` | Yes | Telemetry (`RobotStatusResponse`) |
| POST | `/api/robot/command` | Yes | `RobotCommandRequest`; speed capped; resets the watchdog |
| POST | `/api/robot/stop` | Yes | Controlled stop |
| POST | `/api/robot/emergency-stop` | Yes | Latch emergency stop |
| POST | `/api/robot/reset-emergency` | Yes | Clear the emergency stop latch |

### Configuration

| Variable | Pi | Laptop | Default | Purpose |
|---|---|---|---|---|
| `ROBOT_MODE` | `dogzilla` | `remote` | `simulation` | Selects the `RobotInterface` implementation |
| `ROBOT_API_KEY` | ✓ | ✓ | (none) | Shared secret for `X-Robot-Key` |
| `ROBOT_REMOTE_URL` | | ✓ | `http://barry-pi.local:8100` | Robot API base URL |
| `ROBOT_REMOTE_TIMEOUT` | | ✓ | `0.5` | Per-request timeout in seconds |
| `DOGZILLA_PORT` | ✓ | | `/dev/ttyAMA0` | Serial device for the motion board |
| `DOGZILLA_BAUD` | ✓ | | `115200` | Serial baud rate |
| `ROBOT_MAX_SPEED` | ✓ | | `0.6` | Upper bound on commanded speed (0.0–1.0) |
| `ROBOT_WATCHDOG_MS` | ✓ | | `500` | Auto-stop timeout without commands |

On the Pi these live in `/etc/barry/robot.env` and are loaded by `deploy/pi/barry-robot.service`.

---

## Implementation Status

The two-service split above is the target design. On the laptop, `backend/robot/remote.py` is the HTTP **client** that calls the Pi; on the Pi, `backend/robot/server.py` **is** the Robot API.

> **Provisional contract:** the `RobotInterface` method list, the Robot API endpoints, and the robot schemas described in this document are provisional and will be redesigned. See Phase 0 of [control-plan.md](control-plan.md), which also covers the hand, voice, and LLM control modes.

**Done on `main`:**
- **Pi Robot API v1**: `backend/robot/server.py`, `protocol.py`, `config.py`, `factory.py`, `remote.py`. It has control sessions, command sequence numbers, the watchdog, priority stop and the emergency-stop latch. Routes are under `/api/v1/*` (see [api-specification.md](api-specification.md)).
- **Laptop `/api/v1` relay**: `backend/robot/relay.py` and `relay_router.py`, authenticated with `QUEST_API_KEY` (#54). The Quest sends its key as `X-Robot-Key` (#55).
- **Relay routes for emergency stop, reset, posture, reset and discrete motion** (#69). Emergency stop uses the priority lane, like `/stop`.
- **Pi deployment files**: `deploy/pi/` (systemd unit, `robot.env.example`, README) (#64).
- **Windows PC-VR build** for Quest Link (#61): `Barry → Build PC-VR (Windows)` in the Unity editor, and `frontend/barry-vr/build_pcvr.sh`.
- **Voice prototype, dry run** (#60): `POST /api/v1/voice/command` (OpenAI transcription, fixed command matching). Nothing is sent to the robot.
- **LLM planner prototype, dry run** (#65): `/api/v1/llm/*`, using Anthropic `claude-sonnet-5-5`.
- **LLM brain loop** (#71): `POST /api/v1/llm/plan` starts the loop and returns at once. Poll `GET /api/v1/llm/task`. Caps: `LLM_MAX_STEPS`, `LLM_MAX_TURNS` (8), `LLM_TIMEOUT_S` (60). Failures appear as task state. There is no confirm step. Still runs against a dry-run gateway until #57.
- **Stop and emergency stop need no Quest key** (#72): `POST /api/v1/stop` and `POST /api/v1/emergency-stop` are always allowed, since they can only make the robot safer. The laptop still sends its own `ROBOT_API_KEY` to the Pi.

**In progress:**
- **Command gateway** (#57): one owner of the Pi lease. The voice and LLM routes and the cockpit will switch to it. Until then they use the dry run.
- **Command metadata** (#58): `X-Command-Source` and `X-Command-Id` headers, for audit.

**Not yet built:**
- Emergency mode (Phase 4) and the robot camera (Phase 7).
- Mission authorization on movement.
- `requirements-pi.txt`. The Pi install list is in `deploy/pi/README.md`.
