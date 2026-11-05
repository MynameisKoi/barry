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
   Heavy tasks (YOLO object detection inference, database persistence, mission state, and WebSocket broadcasting) run on the operator laptop to preserve battery and compute on the robotic platform. The Pi runs only the Robot API, with minimal dependencies (`requirements-pi.txt`: no OpenCV or ONNX Runtime).

3. **Single Owner of the Hardware**:
   Only the Pi talks to the DOGZILLA serial port. The Robot API runs as one process (`uvicorn --workers 1`, managed by systemd) and serializes serial writes with a lock, so emergency stop state lives in exactly one place.

4. **Same Subnet Local Area Network**:
   The Quest, laptop, and Pi share the local Wi-Fi.
   - Quest → laptop: `http://<LAPTOP_IP>:8000`, `ws://<LAPTOP_IP>:8000/ws`
   - Laptop → Pi: `ROBOT_REMOTE_URL`, e.g. `http://barry-pi.local:8100` (reserve a fixed DHCP address for the Pi)

   In normal operation the Quest never connects to the Pi directly. The one exception is **emergency mode**, a fallback for when the laptop is unavailable: the Quest sends hand commands straight to the Pi and shows the raw camera feed straight from the Pi (no detection, no LLM) (see Phase 4 of [control-plan.md](control-plan.md)).

5. **Fail-Safe & Safety Architecture**:
   - `emergency_stop` cuts locomotion immediately and sets a latch on the Pi that blocks movement until `reset-emergency` is called. Emergency stop is never gated by missions or auth state on the laptop.
   - **Watchdog**: the Pi stops the robot if no command arrives within `ROBOT_WATCHDOG_MS` (default 500 ms). Clients resend movement commands while a direction is held, so a dropped laptop or Wi-Fi link brings the robot to a halt.
   - **Speed cap**: the Pi limits requested speed to `ROBOT_MAX_SPEED`.
   - **Link loss**: if the Pi is unreachable, `RemoteRobotClient` reports `connected: false` instead of raising, so the Quest shows the robot as offline.
   - Movements are intended to require active mission authorization (enforced on the laptop; planned, not yet implemented in `backend/main.py`).

6. **Security Boundary**:
   Every Robot API route except `/healthz` requires the `X-Robot-Key` header to match `ROBOT_API_KEY`, a shared secret configured on both the laptop and the Pi. The Pi refuses to start in `dogzilla` mode without a key, and the Robot API does not enable CORS because only the laptop backend calls it.

---

## Robot API (Pi)

The Robot API mirrors the laptop's robot paths and response shapes, so `RemoteRobotClient` is a thin HTTP wrapper.

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

Currently the repo contains `RobotInterface`, `RobotSimulator`, and a placeholder `DogzillaController`. Still to build: `backend/robot/config.py`, `factory.py`, `remote.py`, `server.py`, `deploy/pi/`, and `requirements-pi.txt`, plus stop and emergency-stop handling in `DogzillaController.move()`.
