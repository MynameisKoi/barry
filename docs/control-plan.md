# BARRY Control Plan: Hand, Voice, and LLM

Status: **draft for review**. Nothing in this document is implemented yet.

## Goal

The operator controls the DOGZILLA from the Meta Quest 3S in three ways:

| Mode | Input | Best for |
|---|---|---|
| **Hand** | Quest controllers (thumbstick, buttons) | Real-time steering |
| **Voice** | Push-to-talk, fixed command list | Hands-free simple moves |
| **LLM** | Spoken or typed goal, handled by a local LLM on the laptop | Short multi-step tasks |
| **Emergency** | Quest controllers, sent **directly to the Pi**; raw camera feed **directly from the Pi** | Laptop or backend is down: stop the robot or move it to safety |

Hand, voice, and LLM all go through the laptop, where the Quest receives the camera feed and object detection alerts. Emergency mode bypasses the laptop entirely: hand control uses exactly the same controls and command format as normal hand mode (only the destination changes), and the Quest shows the raw camera feed straight from the Pi. Emergency mode has no object detection and no LLM.

## Core idea: one command gateway

Hand, voice, and LLM produce the same kind of command and send it through one **command gateway** on the laptop. The LLM never talks to the Pi. The only time the Quest talks to the Pi is emergency mode (see Phase 4).

```
                 ┌──────────────── QUEST 3S ────────────────┐
                 │  Hand (controllers)   Voice (mic)         │
                 │  sees: camera feed + detection boxes      │
                 └──────┬──────────────────┬─────────────────┘
                        │ WS               │ audio / text
                        ▼                  ▼
┌───────────────────────── LAPTOP ─────────────────────────────┐
│  [Hand input]   [Voice: speech→text→fixed command]  [LLM]    │
│        \                   |                      /          │
│         ▼                  ▼                     ▼           │
│        ┌──────────── COMMAND GATEWAY ────────────┐           │
│        │ • who has control (arbitration)         │           │
│        │ • limits (speed, duration)              │           │
│        │ • logs every command + its source       │──► DB     │
│        └───────────────────┬─────────────────────┘           │
│                            │ remote.py (HTTP client)         │
│  Camera + YOLO detection ──┼──────────────► Quest (all modes) │
└────────────────────────────┼─────────────────────────────────┘
                             ▼ HTTP
                    PI: server.py (Robot API) → motors
                         ▲            │
     hand commands       │            │  raw camera feed
     (same format)       │            ▼  (no detection)
                        QUEST 3S  ── emergency mode only ──
```

`remote.py` runs on the laptop and **calls** the Pi. `server.py` runs on the Pi and **is** the API.

What the gateway guarantees in every mode:
- Stop and emergency stop work from any source, at any time.
- Every action is logged with its source (`hand`, `voice`, `llm`).
- The Pi watchdog and speed cap apply regardless of mode.

---

## Phase 0: Redesign the robot contract (do first)

The current `RobotInterface` method list and the robot API contract (`/api/robot/*`, `/ws` robot messages, `backend/models.py` robot schemas) **are provisional and will be reimplemented.** All later phases depend on the new contract, so this comes first.

### Known problems with the current contract

| # | Problem | Where |
|---|---|---|
| 1 | `move(action: str, ...)` takes a free-form string. `RobotAction` enum exists but `RobotCommandRequest.action` is `str`. | `interface.py`, `models.py` |
| 2 | `stop` / `emergency_stop` are accepted *as move actions* by the simulator but not by `DogzillaController`, which reports success without stopping. Stop/e-stop should never be a `move()` argument. | `simulator.py`, `dogzilla.py` |
| 3 | `speed` (0.0–1.0) has no defined physical meaning: meters per tick in the simulator, step size on DOGZILLA. | all |
| 4 | No duration or distance. One call = one "tick" (1 m or 15° in the simulator). Voice and LLM modes need "move for N seconds" / "turn N degrees". | `interface.py` |
| 5 | `left` / `right` mean *turn*, but DOGZILLA also supports sideways stepping (strafe). Names are ambiguous. | `interface.py` |
| 6 | No command source or command ID, so arbitration and audit can't tell hand / voice / LLM apart. | `interface.py`, `models.py` |
| 7 | Methods are synchronous. A remote HTTP call would block the laptop's async event loop. | `interface.py` |
| 8 | Return type is an untyped `Dict[str, Any]`. Failures all map to HTTP 400, whether the cause is "e-stop active", "robot offline", or "bad input". | `interface.py`, `main.py` |
| 9 | Unknown telemetry is reported as fake values (`battery_level: 0.0`, position `0,0`) instead of "unknown". | `dogzilla.py` |
| 10 | No connect/close lifecycle, no health check, no heartbeat/watchdog concept in the contract. | `interface.py` |
| 11 | Emergency stop is reachable three different ways (`/command` action, WS `emergency_stop`, method) with different response shapes. WS messages have no schema or version. | `main.py` |

### Requirements for the new contract

- **Typed commands:** an enum or typed model for every action, with stop and emergency stop as separate operations.
- **Defined units:** speed as a fraction of a documented max, duration in seconds, turns in degrees.
- **Motion types:** continuous moves (hand mode, kept alive by repeated commands) and bounded moves (voice/LLM, with `seconds` or `degrees`).
- **Separate turn and strafe** actions.
- **Every command carries** `source` (`hand` | `voice` | `llm`) and a `command_id`.
- **Async methods** so `RemoteRobotClient` doesn't block the event loop.
- **Typed results** with an error code (`ESTOP_ACTIVE`, `OFFLINE`, `INVALID`, `BUSY`) that maps to distinct HTTP statuses.
- **Nullable telemetry** for values the hardware can't report.
- **One emergency stop path** with the same response shape everywhere.
- **Versioned WS message schema** (e.g. `"v": 1`) shared by the Quest and the backend.
- **Same contract on the Pi and the laptop,** so `remote.py` is a thin wrapper.
- **Identical hand-command message** on the laptop and the Pi, so the Quest's hand control code is the same in normal and emergency mode; only the target URL changes.
- **Control ownership:** a way for the Pi to know which controller currently owns the robot (`laptop` or `emergency`) and to reject commands from the other.

### Deliverables

- New `backend/robot/interface.py`, plus updated `simulator.py` and `dogzilla.py`.
- Robot schemas in `backend/models.py`, usable by both the Pi and the laptop without importing the database.
- Updated `/api/robot/*` and `/ws` contract in `docs/api-specification.md`.
- Updated `tests/test_robot.py`.
- Unity side updated to the new WS/REST messages. **This is a breaking change for the Quest app.**

---

## Phase 1: Pi Robot API + laptop remote client

- `backend/robot/server.py` (Pi): exposes the new contract over HTTP, with API key, watchdog auto-stop, speed cap, and serial lock.
- `backend/robot/remote.py` (laptop): implements the new interface by calling the Pi; reports `OFFLINE` if the Pi is unreachable.
- `config.py`, `factory.py`, `deploy/pi/` systemd unit and env file, `requirements-pi.txt`.
- Hand control works end to end over the network.

## Phase 2: Command gateway

The gateway enforces these control rules:

| Situation | Rule |
|---|---|
| Stop or emergency stop from any source | Always wins, immediately |
| Hand input while voice or LLM is running | Hand takes over and cancels the running action |
| Voice command while LLM is running | Cancels the LLM task, then runs the voice command |
| Pi unreachable | All modes report "robot offline"; nothing is queued for later |

It also:
- Runs bounded moves ("forward 1 s") by keeping the Pi watchdog fed until the move finishes.
- Logs every command with `source` and `command_id` to the laptop database.
- Shows the active mode and current action in VR.

## Phase 3: Hand mode (finish)

- The thumbstick sends continuous moves about 5 times per second while held; releasing it lets the watchdog stop the robot.
- A dedicated controller button triggers emergency stop.
- The Quest shows a mode indicator and the robot's online/offline state.

## Phase 4: Emergency mode (direct Quest → Pi)

A fallback for when the laptop or its backend is unavailable. Hand control only.

- **Same controls, same message format** as hand mode. The Quest just sends to the Pi's address instead of the laptop's.
- **Separate key:** the Quest app holds an emergency-only key, distinct from the laptop's key, so the Pi knows the source and the key can be revoked on its own.
- **One owner at a time:** while emergency mode is active, the Pi rejects laptop commands. Control returns to the laptop only when the operator exits emergency mode.
- **Stop always works:** emergency stop is accepted from either route at any time.
- **Lower speed cap** (e.g. `ROBOT_EMERGENCY_MAX_SPEED`), because the operator has no detection.
- **Local log on the Pi:** a small append-only file (not a database) of emergency-mode commands, sent to the laptop's audit log when it reconnects.
- **No mission check:** the Pi doesn't hold missions; this is accepted for an emergency fallback.
- **Direct camera feed from the Pi:** the Pi serves the DOGZILLA camera as a raw video stream (e.g. MJPEG, the same format the laptop's `/api/camera/stream` uses today), and the Quest shows it in emergency mode.
  - Video only: no object detection, no overlays from the laptop, no LLM.
  - Protected by the same emergency key as the hand commands.
  - Modest resolution and frame rate (e.g. 640×480 at ~15 fps) so encoding doesn't starve motor control on the Pi.
  - The Pi opens the camera once and serves every viewer from that capture, so the stream to the laptop (Phase 7) and the stream to the Quest don't fight over the device.
- The Quest shows a clear "EMERGENCY MODE: direct to robot" banner.

## Phase 5: Voice mode

- Push-to-talk on the Quest.
- Audio goes to the laptop; speech-to-text runs on the laptop. The transcription model is a prerequisite for this phase and is shared with LLM mode.
- A fixed command list, for example: forward, back, turn left, turn right, stop, emergency stop, "forward N seconds".
- Each command is a bounded move. Stop and emergency stop are matched first.
- Unrecognized speech does nothing. The VR shows "Didn't understand".

## Phase 6: LLM mode

- The LLM runs locally on the laptop.
- Its tool list is **derived from the Phase 0 contract**, not designed separately. Draft:
  - `move(direction, seconds, speed)`
  - `turn(degrees)`
  - `stop()`
  - `get_status()`
  - `get_detections()`, the latest YOLO results
- Every tool call passes through the gateway and its limits.
- Not for real-time steering, because LLM latency is seconds.
- The VR shows the plan and the current step. Any hand input or "stop" cancels the task.

## Phase 7 (optional): Robot camera for normal modes

The Pi camera stream already exists after Phase 4. This phase uses it for normal operation too:

- The laptop reads the Pi stream instead of (or alongside) its own camera.
- The laptop runs detection and re-serves the feed to the Quest with detection alerts.
- In normal modes the Quest still gets video only from the laptop. It reads the Pi stream directly only in emergency mode.

---

## Open questions

1. **Transcription model:** which model to run on the laptop (e.g. a Whisper variant), and whether it must work offline.
2. **Entering emergency mode:** manual only (operator presses a button), or the Quest offers it automatically when the laptop stops responding? Leaning: Quest offers it, operator confirms.
3. **Emergency speed cap:** what value?
4. **Emergency video quality:** what resolution and frame rate are good enough, given the Pi's CPU and Wi-Fi?
5. **Voice vs LLM selection:** an explicit mode switch in VR, or automatic fallback from voice to LLM? Leaning: explicit switch.
6. **LLM confirmation:** run plans immediately, or show them in VR and wait for "confirm"? Leaning: confirm first.
7. **Laptop hardware:** is a GPU available? This determines which local LLM and transcription model are practical.
8. **Camera source for normal modes:** laptop camera (current) or robot camera via the Pi (Phase 7)?
9. **Contract details for Phase 0:** keep REST paths under `/api/robot/*`, or version them (`/api/v2/robot/*`) during the Unity migration?
