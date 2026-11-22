# BARRY: immersive robotic inspection with a DOGZILLA, a Quest 3S and a Flipper Zero

BARRY lets an operator wearing a **Meta Quest 3S** drive and supervise a **DOGZILLA S1/S2** quadruped robot. The operator sees the robot's camera with live YOLO detections, and controls it with the controllers, by voice, or by giving a goal to an LLM. A **Flipper Zero** badge scan starts and ends inspection missions, and every command and event is logged to SQLite.

The system runs as two services:

| Service | Runs on | Entry point | Port | Responsibility |
|---|---|---|---|---|
| Laptop backend | Operator laptop | `backend.main:app` | 8000 | Quest relay (`/api/v1/*`), command gateway, camera + YOLO, voice, LLM brain, missions, Flipper auth, audit DB, cockpits, `/ws` |
| Robot API | DOGZILLA's Raspberry Pi | `backend.robot.server:app` | 8100 | Robot control only: session lease, motion, posture, stop, e-stop latch, watchdog, telemetry |

---

## Architecture

```text
   META QUEST 3S
   Unity app (frontend/barry-vr)  or  WebXR cockpit (/cockpit/vr)
        │  HTTP /api/v1/*, /ws, MJPEG                 │ emergency direct mode only
        ▼                                             │ (same hand commands, raw camera)
┌──────────────────────── LAPTOP :8000 ─────────────────────────┐
│  Hand (relay)     Voice (OpenAI STT)     LLM brain (Claude)   │
│        \                 |                     /              │
│         ▼                ▼                    ▼               │
│        ┌────────── COMMAND GATEWAY ──────────┐                │
│        │ priority: stop/e-stop > hand >      │                │
│        │ voice > LLM; limits; audit logging  │──► SQLite      │
│        └─────────────────┬───────────────────┘                │
│  Camera service + YOLO11n (ONNX) ◄── dog MJPEG feed           │
│  Missions · Flipper Zero listener · AI analysis providers     │
└──────────────────────────┼────────────────────────────────────┘
                           │ HTTP + X-Robot-Key
                           ▼
              RASPBERRY PI :8100  Robot API ── serial ──► DOGZILLA board
              (watchdog, speed cap, e-stop latch)   ▲
                                                    └── Quest, emergency mode
```

- **One command gateway** (`backend/gateway/gateway.py`). Hand, voice and LLM commands all pass through it. It holds the Pi session, decides who has control, applies limits and logs each command with its source (`hand`, `voice`, `llm`, `emergency`).
- **The robot sits behind `RobotInterface`.** `ROBOT_MODE` selects `remote` on the laptop (calls the Pi over HTTP) and `dogzilla` on the Pi (serial to the board).
- **Safety lives on the Pi too.** The watchdog stops the robot when commands stop arriving (`ROBOT_WATCHDOG_MS`, default 500 ms), speed is capped (`ROBOT_MAX_SPEED`), and an emergency stop latches until it is reset. `POST /api/v1/stop` and `POST /api/v1/emergency-stop` need no Quest key and use a priority lane.
- **Camera.** With `ROBOT_MODE=remote` the laptop reads the dog's MJPEG feed (`DOG_VIDEO_URL`, else port 6500 on the `ROBOT_REMOTE_URL` host), runs YOLO11n on it and re-serves it at `/api/v1/camera/stream`. Detections are at `/api/ai/detections`.

Full design: [docs/architecture.md](docs/architecture.md) and [docs/control-plan.md](docs/control-plan.md).

### Control modes

| Mode | Input | Path |
|---|---|---|
| Hand | Quest thumbsticks and buttons | Quest → `/api/v1/motion` → gateway → Pi |
| Voice | Push-to-talk (hold Y) | `POST /api/v1/voice/command` → OpenAI transcription → fixed command grammar → gateway; anything else goes to the LLM |
| LLM | Spoken or typed goal | `POST /api/v1/llm/plan` → Anthropic `claude-sonnet-5-5` tool loop → gateway (no confirm step; any hand input or stop cancels it) |
| Find object | "find a ball" | `POST /api/v1/llm/find`: camera snapshots + YOLO, 120 s per search, then WATCH and re-search |
| Emergency direct | Quest → Pi `:8100` | Fallback when the laptop is down: same hand commands, raw camera, no detection or LLM |

### Clients

- **Unity app** (`frontend/barry-vr/`, Unity `6000.6.8f1`): Quest APK and Windows PC-VR build. See [docs/quest3s-setup-guide.md](docs/quest3s-setup-guide.md).
- **WebXR cockpit** (`/cockpit/vr`, `backend/static/cockpit_vr.html`): a 1:1 port of the Unity dashboard that runs in the Quest Browser. Unity layout changes must be mirrored here (see [AGENTS.md](AGENTS.md)).
- **2D web cockpit** (`/cockpit`): desktop browser; still uses the legacy `/api/robot/*` routes.

---

## Repository layout

| Path | Contents |
|---|---|
| `backend/main.py` | Laptop FastAPI app: lifespan, routers, `/ws`, missions, events, AI routes |
| `backend/gateway/` | `CommandGateway` and live detections |
| `backend/robot/` | `RobotInterface`, `dogzilla.py`, Pi `server.py`, `protocol.py`, laptop `remote.py` / `relay*.py` |
| `backend/camera/` | Camera service, sources (Yahboom, dog/Pi MJPEG), `/api/v1/camera/*` |
| `backend/ai/` | Provider layer and orchestrator; `control/` (LLM brain, find mode); `vision/` (YOLO detector and loop) |
| `backend/voice/` | Transcription and command grammar |
| `backend/auth/`, `backend/missions/`, `backend/services/` | Flipper Zero listener, operator auth, missions and reports |
| `backend/static/` | `cockpit.html`, `cockpit_vr.html`, self-hosted three.js and fonts |
| `frontend/barry-vr/` | Unity project |
| `deploy/pi/` | systemd unit, `robot.env.example` and install steps for the Pi |
| `scripts/`, `tools/` | YOLO export, Flipper / RFID helpers, live workflow audit |
| `tests/` | pytest suite |
| `docs/` | Design and setup documents (index below) |

---

## Quick start

Requires Python 3.10+.

**1. Robot API on the Pi.** Install it as a systemd service with [deploy/pi/README.md](deploy/pi/README.md). It runs `uvicorn backend.robot.server:app --host 0.0.0.0 --port 8100 --workers 1` with `ROBOT_MODE=dogzilla` and `ROBOT_API_KEY` set in `/etc/barry/robot.env`.

**2. Backend on the laptop.**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set ROBOT_MODE=remote, ROBOT_REMOTE_URL, ROBOT_API_KEY, API keys; never commit .env

ROBOT_MODE=remote ROBOT_REMOTE_URL=http://<pi-ip>:8100 ROBOT_API_KEY=<pi key> \
  uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

`ROBOT_API_KEY` must match the Pi's. The camera feed is read from the dog at `http://<pi-ip>:6500/video_feed` unless `DOG_VIDEO_URL` is set.

Then open:

| URL | What |
|---|---|
| `http://localhost:8000/cockpit` | 2D web cockpit |
| `http://<laptop-ip>:8000/cockpit/vr` | WebXR cockpit (open in the Quest Browser) |
| `http://localhost:8000/docs` | OpenAPI / Swagger UI |
| `http://localhost:8000/api/v1/status` | Link, lease and watchdog state |
| `http://localhost:8000/api/ai/status` | AI provider status |

---

## Configuration

All settings are environment variables; `.env.example` lists them. The main ones:

| Variable | Default | Purpose |
|---|---|---|
| `ROBOT_MODE` | | `remote` on the laptop, `dogzilla` on the Pi |
| `ROBOT_REMOTE_URL` | `http://barry-pi.local:8100` | Pi Robot API base URL |
| `ROBOT_API_KEY` | (none) | Shared laptop ↔ Pi secret, sent as `X-Robot-Key` |
| `QUEST_API_KEY` | (none) | Key the Quest sends to the laptop's `/api/v1/*` |
| `CAMERA_API_KEY` | (none) | Key for `/api/v1/camera/*` |
| `CAMERA_DEVICE`, `CAMERA_SOURCE`, `DOG_VIDEO_URL` | | Camera source selection (`dog`, `pi`, local) |
| `ANTHROPIC_API_KEY` | (none) | LLM brain and find mode |
| `OPENAI_API_KEY` | (none) | Voice transcription |
| `LLM_PROVIDER`, `FALLBACK_LLM_PROVIDER` | `mock` | Event analysis / reports provider ([docs/ai_provider_setup.md](docs/ai_provider_setup.md)) |

> ⚠️ **Testing defaults (kept by decision on 2026-11-12).** `.env.example` ships with `REQUIRE_ACTIVE_MISSION=false`, `AUTH_ALLOW_UNREGISTERED_TAGS=true`, `QUEST_API_KEY_ALLOW_ANONYMOUS=true` and `CAMERA_API_KEY_ALLOW_ANONYMOUS=true`. With these, **anyone on the Wi-Fi can drive the robot and watch its camera**, and any scanned tag becomes an operator. Use them only on a private network.

---

## Tests

```bash
.venv/bin/pytest -q tests backend/camera
```

On `main` at `9908e00`: **606 passed** (about 1 minute). Tests never touch real hardware.

---

## Documentation

| Document | Covers |
|---|---|
| [docs/control-plan.md](docs/control-plan.md) | Design reference: hand, voice, LLM and emergency modes; find-object mode |
| [docs/build-plan.md](docs/build-plan.md) | Phases 1–7 and their scope |
| [docs/architecture.md](docs/architecture.md) | Services, topology, safety and security boundaries |
| [docs/api-specification.md](docs/api-specification.md) | `/api/v1/*` relay, Pi Robot API, camera, voice and LLM routes |
| [docs/api_contracts.md](docs/api_contracts.md) | Older auth, mission, event and AI routes, and `/ws` messages |
| [docs/setup-guide.md](docs/setup-guide.md) | Laptop setup, anonymous dev mode, `barry_config.json` |
| [docs/quest3s-setup-guide.md](docs/quest3s-setup-guide.md) | Quest developer mode, ADB, Unity APK and PC-VR builds |
| [docs/ai_provider_setup.md](docs/ai_provider_setup.md) | YOLO model, AI providers, DGX Spark |
| [docs/joystick_and_flipper_mission_workflow.md](docs/joystick_and_flipper_mission_workflow.md) | Flipper Zero badge → mission lifecycle |
| [docs/team_testing_and_verification_notes.md](docs/team_testing_and_verification_notes.md) | Hardware and headset test checklists |
| [docs/development_guide.md](docs/development_guide.md) | Test modules and `curl` walkthrough |
| [deploy/pi/README.md](deploy/pi/README.md) | Installing the Robot API on the Pi |
| [AGENTS.md](AGENTS.md) | Team and PR rules for contributors and agents |

---

## Contributing

One branch and one PR per task; never commit to `main`. Run the tests that cover your change and list them in the PR. Never weaken emergency-stop, stop, watchdog or speed-cap behaviour. Full rules are in [AGENTS.md](AGENTS.md).
