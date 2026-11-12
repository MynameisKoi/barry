# BARRY Control Plan: Hand, Voice, and LLM

Status: **design reference.** Phase 0 is mostly done (Pi Robot API v1). Voice mode (Phase 5) now covers both fixed commands and spoken conversation with the LLM, and ranks above the LLM. The build order, current state and per-phase work are in [build-plan.md](build-plan.md).

## Goal

The operator controls the DOGZILLA from the Meta Quest 3S in three ways:

| Mode | Input | Best for |
|---|---|---|
| **Hand** | Quest controllers (thumbstick, buttons) | Real-time steering |
| **Voice** | Push-to-talk, fixed command list | Hands-free simple moves |
| **LLM** | Spoken or typed goal, handled by a cloud LLM (Anthropic `claude-sonnet-5-5`) called from the laptop | Short multi-step tasks |
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
- Audio goes to the laptop; speech-to-text is the OpenAI transcription API (cloud), called from the laptop. Anthropic has no speech-to-text API. The transcription model is a prerequisite for this phase and is shared with LLM mode. (Decided 2026-11-10.)
- A fixed command list, for example: forward, back, turn left, turn right, stop, emergency stop, "forward N seconds".
- Each command is a bounded move. Stop and emergency stop are matched first.
- Unrecognized speech does nothing. The VR shows "Didn't understand".

## Phase 6: LLM mode

- The LLM is a cloud API: Anthropic `claude-sonnet-5-5`, called from the laptop (Decided 2026-11-10).
- **Plans run immediately with no confirm step.** This supersedes the confirm steps in Phase 5 and below. The safety net is the gateway's limits (bounded moves, speed caps), and "stop" or any hand input cancels the task.
- Voice and LLM modes need internet access and `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` in `.env`. Hand and emergency modes are unaffected.
- Its tool list is **derived from the Phase 0 contract**, not designed separately. Draft:
  - `move(direction, seconds, speed)`
  - `turn(degrees)`
  - `stop()`
  - `get_status()`
  - `get_detections()`, the latest YOLO results
- Every tool call passes through the gateway and its limits.
- Not for real-time steering, because LLM latency is seconds.
- The VR shows the plan and the current step. Any hand input or "stop" cancels the task.

### Phase 6b: autonomous "find an object" mode (decided 2026-11-12)

**Tom's decisions**

1. The autonomous mode is **find an object**: the operator names it ("find a ball") and the brain drives the dog by itself.
2. **120 s per search.** A search that reaches 120 s without finding the object ends the session as "not found".
3. **Camera snapshots are sent to Claude** (vision) in addition to the YOLO detections.
4. **No operator-supervision requirement for now** (no headset-presence dead-man). The preemption stays: any hand input, STOP, voice "stop" or e-stop ends the run at once, and a dedicated **Stop finding** button does too.
5. **Vertical look:** `look()` can tilt the body (pitch, clamped to the safe attitude limit of 10 deg) and change the body height (low / medium / high); the scan is level, then down, then up at each heading.
6. **WATCH + automatic re-search:** after the object is found the dog stands still and watches with YOLO only (no Claude call); if the object is unseen for more than 2 s a new search starts by itself. The whole session is capped by `LLM_FIND_SESSION_S` (**300 s = 5 min while testing**; Tom can raise it).
7. Common names map to COCO labels (ball -> sports ball, phone -> cell phone, sofa -> couch, ...); anything else is left to the model to recognise in the image.

**How it works** (`backend/ai/control/`): the same brain loop as Phase 6 with a find tool set (`look`, `move`, `turn`, `found`, `stop`) and limits enforced by the executor (a rejected call is never executed, whatever the model says):

| Rule | Value |
|---|---|
| Move | <= 1.0 s at speed <= 0.3 |
| Turn | <= 45 deg |
| `look()` first | before the first motion and between any two motions (a violating call is rejected) |
| Body posture | level / medium is restored automatically before any `move` and when the run ends (after the stop) |
| `look(pitch_deg, height)` | pitch clamped to +/-10 deg (`ATTITUDE_SAFE_LIMITS["pitch"]`); settles ~0.5 s before the photo; the response reports the posture actually used |
| Search budget | 120 s, 40 model turns, 60 steps (`LLM_FIND_*`) |
| Camera | a frame older than 2 s, no camera, or an unreadable frame ends the run ("camera lost") with a stop |
| Always stop | found/lost/timeout/camera lost/refusal/error/cancel: a `stop` is sent (a hand-input preemption leaves the stop to the operator's own command, which owns the robot) |

`look()` returns an **image block inside the tool_result** (`{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", ...}}`, <= 640 px wide, JPEG q70) next to a text block with the YOLO detections, the frame age and the posture. YOLO is also checked after each look: the target at confidence >= 0.5 (`LLM_FIND_CONFIDENCE`) ends the search at once, so the dog does not walk past it.

**Session**: SEARCHING (Claude loop) -> found -> WATCHING (stop, level posture, YOLO about once a second, no model calls) -> target unseen > 2 s -> LOST -> a fresh SEARCHING (a new conversation) -> ... until the session cap, a search that ends not-found, or the operator ends it.

**API** (all need `X-Robot-Key`):

- `POST /api/v1/llm/find {"object": "ball"}` -> `{"status": "started", "task": {...}}`. The object is 1-3 words of letters (spaces, `-`, `'`), checked on the raw text. `/plan` and voice goals like "find a chair" route here too.
- `POST /api/v1/llm/find/stop` -> `{"stopped": true, "task": {...}}`: the **Stop finding** button (search or watch): stop, posture reset, outcome `stopped_by_operator`. `POST /api/v1/llm/task/cancel` also ends a find session.
- `GET /api/v1/llm/task` for a find session adds: `mode: "find"`, `target`, `phase` (`searching | watching | lost`), `seconds_left` (this search), `timeout_s`, `session_left`, `session_s`, `searches`, `found {label, where}`, `outcome` (`found | not_found | timeout | session_end | camera_lost | stopped_by_operator | cancelled | refused | error | limit`), `posture {pitch_deg, height}`, `model_calls`, `input_tokens`, `output_tokens`, and per step `note` (the model's short reason).
- Each find run is logged as an `llm_find_run` event (goal, outcome, duration, steps, looks, searches, model calls, tokens).

**Env** (see `.env.example`): `LLM_FIND_TIMEOUT_S=120`, `LLM_FIND_SESSION_S=300`, `LLM_FIND_MAX_TURNS=40`, `LLM_FIND_MAX_STEPS=60`, `LLM_FIND_CONFIDENCE=0.5`, `LLM_FIND_LOST_S=2`, `LLM_FIND_WATCH_S=1`, `LLM_FIND_SETTLE_S=0.5`.

**Open: pitch sign.** `look(pitch_deg)` uses a logical convention, **positive = camera UP, negative = DOWN** (stated in the tool description). The simulator only echoes the commanded pitch and has no camera, so the sign cannot be verified in simulation; which sign tilts the real camera down is to be confirmed on the dog and mapped inside the gateway/robot layer (#135), not in the brain.

## Phase 7 (optional): Robot camera for normal modes

The Pi camera stream already exists after Phase 4. This phase uses it for normal operation too:

- The laptop reads the Pi stream instead of (or alongside) its own camera.
- The laptop runs detection and re-serves the feed to the Quest with detection alerts.
- In normal modes the Quest still gets video only from the laptop. It reads the Pi stream directly only in emergency mode.

---

## Open questions

1. **Transcription model:** which model to run on the laptop (e.g. a Whisper variant), and whether it must work offline. **Decided 2026-11-10:** OpenAI transcription API (cloud); not offline.
2. **Entering emergency mode:** manual only (operator presses a button), or the Quest offers it automatically when the laptop stops responding? Leaning: Quest offers it, operator confirms.
3. **Emergency speed cap:** what value?
4. **Emergency video quality:** what resolution and frame rate are good enough, given the Pi's CPU and Wi-Fi?
5. **Voice vs LLM selection:** an explicit mode switch in VR, or automatic fallback from voice to LLM? Leaning: explicit switch.
6. **LLM confirmation:** run plans immediately, or show them in VR and wait for "confirm"? Leaning: confirm first. **Decided 2026-11-10:** run plans immediately, no confirm step; the gateway's limits and stop/hand input are the safety net.
7. **Laptop hardware:** is a GPU available? This determines which local LLM and transcription model are practical. **Decided 2026-11-10:** not needed; LLM and speech-to-text are both cloud.
8. **Camera source for normal modes:** laptop camera (current) or robot camera via the Pi (Phase 7)?
9. **Contract details for Phase 0:** keep REST paths under `/api/robot/*`, or version them (`/api/v2/robot/*`) during the Unity migration?
