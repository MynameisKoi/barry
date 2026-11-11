# BARRY Setup & Development Guide

This guide describes how to run and verify the backend environment on the primary laptop.

---

## 1. Prerequisites
- Python 3.10+ (tested on Python 3.13)
- Git
- Local Wi-Fi network connection (shared between laptop and Meta Quest 3S)

---

## 2. Environment Setup

### Clone Repository & Navigate
```bash
cd <repo>
```

### Create and Activate Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Install Dependencies
```bash
pip install -r requirements.txt
```

---

## 3. Running Backend Server

### Development Mode (with hot-reload)
```bash
source .venv/bin/activate
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Accessing Interactive Docs
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## 4. Running Automated Tests
```bash
source .venv/bin/activate
pytest -v
```

---

## 5. Finding Laptop IP for Meta Quest 3S
Meta Quest 3S connects to the laptop over the local Wi-Fi router.

Find your local IP address:
```bash
hostname -I | awk '{print $1}'
```
Use this IP in Unity scripts:
- REST Base URL: `http://<YOUR_IP>:8000`
- WebSocket URL: `ws://<YOUR_IP>:8000/ws`

---

## 6. Dev Quickstart: Anonymous Mode, Pi Simulator, and `barry_config.json`

For VR development on the dev laptop, the Quest and PC-VR clients currently run in **anonymous mode**. The web cockpit is keyless too (#108), and the PC-VR build has no key.

> ⚠️ **Anonymous mode lets anyone on the Wi-Fi drive the robot, and watch the robot camera.** Anonymous mode turns off the Quest key (`QUEST_API_KEY_ALLOW_ANONYMOUS`) and the camera key (`CAMERA_API_KEY_ALLOW_ANONYMOUS`). Use it only for development, and never on a shared network.

Run all commands from the repository root with the virtual environment active.

### 6.1 Real Pi

```bash
ROBOT_MODE=remote ROBOT_REMOTE_URL=http://<pi-ip>:8100 ROBOT_API_KEY=<pi key> QUEST_API_KEY_ALLOW_ANONYMOUS=true CAMERA_API_KEY_ALLOW_ANONYMOUS=true uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

`<pi key>` must match `ROBOT_API_KEY` in the Pi's `/etc/barry/robot.env`.

### 6.2 No Pi: simulator on the laptop

Start the Pi API in simulator mode on the laptop, in its own terminal:

```bash
ROBOT_MODE=simulator ROBOT_API_KEY=devkey uvicorn backend.robot.server:app --port 8100
```

Then start the backend and point it at the simulator:

```bash
ROBOT_MODE=remote ROBOT_REMOTE_URL=http://127.0.0.1:8100 ROBOT_API_KEY=devkey QUEST_API_KEY_ALLOW_ANONYMOUS=true CAMERA_API_KEY_ALLOW_ANONYMOUS=true uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Setting `ROBOT_MODE=simulation` on the laptop alone does **not** move anything through `/api/v1` until PR #98 merges. Use the two-process setup above.

**Missions:** once PR #98 is merged, motion needs an active mission. Start one from the headset's mission panel, or with `POST /api/mission/start`. Stop and e-stop never need a mission.

### 6.3 PC-VR exe: `barry_config.json`

Put `barry_config.json` in the same folder as `Barry.exe`. The app reads it at startup and overlays the values on its defaults:

```json
{"backendUrl":"http://<laptop-ip>:8000","cameraStreamUrl":"http://<laptop-ip>:8000/api/v1/camera/stream"}
```

Find `<laptop-ip>` with `hostname -I | awk '{print $1}'` (Section 5).

### 6.4 Camera stream

The camera route `/api/v1/camera/stream` has its own key, so the Quest key setting does not cover it. Without `CAMERA_API_KEY` or `CAMERA_API_KEY_ALLOW_ANONYMOUS=true`, `/api/v1/camera/status` returns 503. Setups 6.1 and 6.2 set both variables on the laptop backend. With only `QUEST_API_KEY_ALLOW_ANONYMOUS=true`, the status check returns 503; adding `CAMERA_API_KEY_ALLOW_ANONYMOUS=true` returns 200 (`backend/camera/auth.py`).
