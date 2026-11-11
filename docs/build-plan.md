# BARRY Build Plan: Phases 1–7

Status: **agreed scope, not started.** This plan turns [control-plan.md](control-plan.md) into build work, based on the code on `main` as of 2026-11-10 (commit `6a939c7`).

## Scope

| Phase | Name | In scope |
|---|---|---|
| 0 | Contract redesign | Mostly done (Pi API v1). The remaining items are folded into Phases 1 and 2. |
| 1 | Pi Robot API deployment + laptop client | **Yes** (start here) |
| 2 | Command gateway | **Yes** |
| 3 | Hand mode (finish) | **Yes** |
| 4 | Emergency mode (Quest → Pi direct) | **Yes** |
| 5 | Voice mode | **Yes.** Spoken fixed commands **and** spoken conversation with the LLM. Voice outranks the LLM. Speech-to-text: OpenAI transcription API (cloud). **Decided 2026-11-10.** |
| 6 | LLM control mode | **Yes.** Goals come from voice (Phase 5) or typed text. Cloud LLM: Anthropic `claude-sonnet-5-5`. Plans run without a confirm step. **Decided 2026-11-10.** |
| 7 | Robot camera for normal modes | **Yes** |

Phase numbers match `control-plan.md` so the two documents can be read side by side.

Order: 1 → 2 → 3 → 4 → 5 → 6 → 7. Phase 4 can start once Phase 2's ownership work is merged. Phase 5 ships the fixed voice commands first; its LLM conversation part is wired up as soon as Phase 6's planner exists, so 5 and 6 overlap. Phase 7 depends on the Pi camera from Phase 4. Phase 6 needs live detections from Phase 7 for `get_detections()`; until then it uses the simulated detections.

**Control priority (highest first):** stop / e-stop from any source → hand → voice command → LLM task. Voice is *over* the LLM: a recognised voice command cancels a running LLM task, and voice is also how the operator talks to the LLM. LLM plans run without a confirm step (Phase 6).

## What already exists (do not rebuild)

- **Pi Robot API v1** ([server.py](../backend/robot/server.py), wire contract in [protocol.py](../backend/robot/protocol.py)): `X-Robot-Key` auth, single-operator lease (`/api/v1/session`), `X-Command-Seq` stale-command rejection, watchdog, stop priority, emergency-stop latch and reset, motion (analog and discrete), movement style, posture, allowlisted actions, telemetry, status.
- **Laptop**: [remote.py](../backend/robot/remote.py), [config.py](../backend/robot/config.py), [factory.py](../backend/robot/factory.py), and a transparent `/api/v1/*` relay to the Pi ([relay.py](../backend/robot/relay.py), [relay_router.py](../backend/robot/relay_router.py)).
- **Camera** module ([backend/camera/](../backend/camera/)): `YahboomSource`, key-protected MJPEG router (`/api/v1/camera/*`), viewer limit.
- **Unity** ([frontend/barry-vr/](../frontend/barry-vr/)): `BarryApiClient` on `/api/v1`, MJPEG view, controller input with a STOP button, teleop state machine sending motion at 5–20 Hz, Quest and PC-VR build setup.
- **AI**: provider layer (mock, OpenRouter, local, OpenAI, Anthropic, Google) used for event analysis and reports only.

---

## Phase 1: Pi deployment + laptop client

**Goal:** the real robot runs the Pi API as a managed service, and the laptop drives it end to end with the Quest's hand controls.

Work:
1. `requirements-pi.txt`: FastAPI, uvicorn, pydantic, pyserial only. Camera dependencies are added in Phase 4.
2. `deploy/pi/barry-robot.service`: systemd unit, `uvicorn backend.robot.server:app --host 0.0.0.0 --port 8100 --workers 1`, `Restart=on-failure`, runs as a non-root user in the `dialout` group.
3. `deploy/pi/robot.env.example` → installed as `/etc/barry/robot.env`: `ROBOT_MODE=dogzilla`, `ROBOT_API_KEY`, `DOGZILLA_PORT`, `ROBOT_WATCHDOG_MS`, `ROBOT_SESSION_TTL_S`, `ROBOT_MAX_COMMAND_AGE_MS`.
4. `deploy/pi/README.md`: install steps, reserving a fixed DHCP address, checking `/healthz`, stopping the Yahboom `app_dogzilla.py` service so only one process owns the serial port.
5. Relay: add the routes the Quest can't reach today: `emergency-stop`, `emergency-stop/reset`, `posture`, `reset`, `motion/discrete`. `emergency-stop` must use the priority client like `stop`.
6. Docs: update [architecture.md](architecture.md) "Implementation Status", rewrite [api-specification.md](api-specification.md) for `/api/v1/*`, fix step 5 of [quest3s-setup-guide.md](quest3s-setup-guide.md) (project is `frontend/barry-vr/`, real script names), and replace `/home/koiisme` paths in the setup guides.

Done when:
- Robot lifted on a stand: Quest thumbstick → laptop relay → Pi → legs move; releasing the stick stops within `ROBOT_WATCHDOG_MS`.
- Pulling the laptop's Wi-Fi stops the robot.
- `tests/test_robot_relay.py` covers every new relayed route, including e-stop through the priority client.

## Phase 2: Command gateway

**Goal:** every robot command from the laptop goes through one place that decides who has control, applies limits, and logs the source.

**Key design change:** today the relay passes the Quest's session and sequence headers straight to the Pi, so the **Quest** holds the Pi lease. The LLM runs on the laptop and can't share that lease. From Phase 2 the **gateway holds the single Pi lease** and the Quest gets a gateway-level session with the same API shape. The Quest code does not change.

Work:
1. `backend/gateway/` (new package):
   - `gateway.py`: `CommandGateway` that owns the Pi session through `RemoteRobotClient` (`remote` mode) or the local robot (`simulation` mode).
   - Arbitration rules from `control-plan.md`: stop/e-stop always wins; hand input cancels a running voice move or LLM task; a voice command cancels a running LLM task; Pi unreachable → `OFFLINE` for every source, nothing queued.
   - Bounded moves: `run_bounded(direction, seconds, speed)` resends motion at 10 Hz until the time is up, then stops. Turns by degrees use measured yaw from telemetry when available, otherwise a calibrated time estimate (`ROBOT_TURN_DEG_PER_S`).
   - Limits: `GATEWAY_MAX_SPEED`, `GATEWAY_MAX_BOUNDED_S`.
   - **Mission check:** motion needs an active mission; stop and e-stop never do. This closes the "planned, not yet implemented" item in `architecture.md`.
2. Command metadata as **headers**, not body fields (the Pi models use `extra="forbid"`, and hand commands must stay identical on laptop and Pi): `X-Command-Source: hand|voice|llm|emergency`, `X-Command-Id: <uuid>`. The Pi accepts and logs them; the gateway sets them for its own calls.
3. `relay_router.py` routes through the gateway instead of forwarding blindly.
4. Audit: each command is logged with `source`, `command_id`, result code, and latency.
5. WebSocket v1 messages (`"v": 1`): `control_state` (owner, active source, current action, robot online/offline) broadcast on change and at 1 Hz. Retire the legacy `robot_command` WS message and `/api/robot/command` once the web cockpits are migrated (see "Cleanup").

Done when:
- Tests: hand input cancels a bounded move; a voice command cancels an LLM task; stop interrupts a bounded move within one tick; motion without a mission → 403-style error; Pi down → `OFFLINE` and no retry queue; every command row in the DB has a source and id.

## Phase 3: Hand mode (finish)

**Goal:** the Quest app is a complete hand-control client.

Work (Unity, `frontend/barry-vr/`):
1. `BarryApiClient`: add `EmergencyStop()`, `ResetEmergency()`, `SetPosture()`, `Reset()`.
2. Controller mapping: a dedicated emergency-stop input (proposal: both grips plus the STOP button held together, so it can't be pressed by accident). It's separate from the existing STOP button, which stays as the normal stop.
3. WebSocket client (`Barry.Api/BarryWsClient.cs`): connect to `/ws`, parse v1 messages, reconnect with backoff.
4. HUD: mode indicator (HAND / VOICE / LLM / EMERGENCY), robot online/offline, emergency-stop latched state with a reset action that requires confirmation.
5. Tests (EditMode): e-stop input priority, WS message parsing, HUD models.

Done when: on the Quest, the operator can drive, stop, e-stop, and reset while seeing the mode and link state, all through the gateway.

## Phase 4: Emergency mode (Quest → Pi direct)

**Goal:** if the laptop is down, the operator can still stop the robot or move it to safety from the Quest.

Pi (`backend/robot/`):
1. `ROBOT_EMERGENCY_KEY`: a second key, separate from `ROBOT_API_KEY`, so it can be revoked on its own.
2. Control owner: `laptop` or `emergency`. Taking emergency ownership (`POST /api/v1/emergency/enter`) ends the laptop's lease; while it is active, the Pi rejects laptop commands with `409 OWNER_MISMATCH`. `POST /api/v1/emergency/exit` hands control back. **Stop and e-stop are accepted from either key at all times.**
3. `ROBOT_EMERGENCY_MAX_SPEED` (proposal: 0.3), applied on the Pi while owner is `emergency`.
4. Local append-only log (`/var/lib/barry/emergency.jsonl`); the gateway pulls and imports it into the audit DB when it reconnects (`GET /api/v1/emergency/log?since=`).
5. Camera on the Pi: mount the existing `backend/camera` router in the Pi app, using `YahboomSource` at 640×480, ~15 fps. The camera is opened once and shared by all viewers (Quest now, laptop in Phase 7). Protected by the emergency key or the laptop key. Adds `opencv-python-headless` to `requirements-pi.txt` (update the "no OpenCV on the Pi" line in `architecture.md`).

Quest:
6. Emergency mode switches the client's base URL to the Pi (`http://<PI_IP>:8100`) and its key to the emergency key. Hand-control code is unchanged because the paths and body shapes are the same.
7. Entry: when the laptop has not answered for 3 s, the Quest offers emergency mode and the operator confirms. Full-width "EMERGENCY MODE: direct to robot" banner; camera switches to the Pi stream; no detections and no LLM.

Done when:
- With the laptop backend stopped, the Quest enters emergency mode, drives at the lower cap, sees the Pi camera, and e-stops.
- Laptop commands are rejected while emergency mode is active; stop from the laptop still works.
- After exit and reconnect, emergency commands appear in the laptop audit log.

## Phase 5: Voice mode (commands + LLM conversation)

**Goal:** the operator speaks to BARRY. Short fixed phrases move or stop the robot at once; anything else goes to the LLM, which answers out loud and can propose a plan. Voice outranks the LLM.

**Flow for one utterance:**

```
Quest: hold push-to-talk → record → release → upload audio
Laptop: speech-to-text → transcript
        1. fixed command grammar   ── match ──► gateway (source "voice"), cancels any LLM task
        2. cancel phrase            ── "cancel" ──► cancels the running LLM task
        3. anything else            ──► LLM conversation (Phase 6): spoken answer and/or a plan that runs immediately
Laptop → Quest: transcript, what happened, reply text (+ reply audio)
```

The grammar is checked before the LLM, so a recognised command never waits on the LLM, and it cancels whatever the LLM is running.

Work:
1. **Quest capture** (Unity): push-to-talk on a controller button (proposal: left trigger held). Record with Unity's `Microphone` API at 16 kHz mono, max 10 s, upload as WAV to `POST /api/v1/voice/utterance`. Request `RECORD_AUDIO` in `QuestBuildSetup.cs`. A small "listening" indicator while the button is held.
2. **Speech-to-text** on the laptop: `backend/voice/stt.py` behind a small interface, first implementation the OpenAI transcription API (cloud; key `OPENAI_API_KEY`, model set by `VOICE_STT_MODEL`), plus a fake for tests. Decided 2026-11-10: Anthropic has no speech-to-text API, so `faster-whisper` is no longer the plan.
3. **Command grammar** `backend/voice/commands.py`: normalise the transcript, then match in this order:
   - `stop`, `halt` → stop; `emergency stop` → e-stop (matched first)
   - `forward` / `back` / `left` / `right` / `turn left` / `turn right`, optionally with `for N seconds` or `N degrees` → bounded move through the gateway
   - `slow` / `normal` / `fast` → movement style; named allowlisted actions
   - `cancel` → cancels the running LLM task (no-op if none is running). `stop` above also cancels it.
   Anything with no match goes to the LLM. Low-confidence transcripts (Whisper `no_speech_prob` or average log-prob below a threshold) are dropped with "Didn't understand", never sent to the LLM as commands.
4. **LLM conversation** `backend/voice/router.py` → Phase 6 planner with a `conversation` mode: the LLM may answer a question ("what do you see?", "what's the battery?") using the read-only tools, and/or return a plan. Plans from voice run immediately, the same as typed ones, with no confirm step (see Phase 6). Keep the last few turns per session as context.
5. **Spoken reply**: text-to-speech on the laptop (`backend/voice/tts.py`, first implementation Piper, offline), returned as a WAV URL the Quest plays. The reply text is always shown in VR too, so a TTS failure never hides the answer.
6. **API**: `POST /api/v1/voice/utterance` → `{transcript, kind: command|cancel|conversation|not_understood, command_result?, reply_text?, reply_audio_url?}`. WS message `voice` with the same fields so other viewers (web cockpit) see it.
7. **VR**: transcript line, "Didn't understand", reply subtitles, mode indicator shows VOICE while a voice move runs.

**Safety notes:**
- Voice stop goes through STT, so it takes about 1–2 s. It is a convenience, not the safety stop; the controller STOP and e-stop inputs stay the primary way to stop.
- Voice never resets an emergency stop. Reset stays a confirmed controller/UI action.
- Voice is not available in emergency mode (no laptop).

Done when:
- Tests with a fake STT: each grammar phrase maps to the right gateway call with source `voice`; "stop" during an LLM task cancels it and stops; "cancel" during an LLM task cancels it without a robot command; an unmatched phrase reaches the LLM; a low-confidence transcript does nothing.
- On the Quest: "forward two seconds" moves the simulator; "what do you see?" gets a spoken answer; "go check the door" produces a plan and the robot starts it right away; "stop" ends it.

## Phase 6: LLM control mode

**Goal:** the operator gives a short goal ("walk forward two seconds, turn right, and report what you see") and the LLM turns it into a plan the gateway runs.

**Decided 2026-11-10:** the LLM is the cloud Anthropic API, `claude-sonnet-5-5` (key `ANTHROPIC_API_KEY`), not a local model. **Plans run immediately with no confirm step.** Phases 5 and 6 contain no confirm step for LLM plans; "cancel" and "stop" are the controls. The safety net is the command gateway's limits (Phase 2: bounded moves, speed caps), and "stop" or any hand input cancels the task. Voice and LLM modes need internet access; hand and emergency modes are unaffected. The laptop GPU is no longer needed for LLM or speech-to-text.

Input: **voice is the main input** (Phase 5 sends every non-command utterance here). Typed goals from the laptop web cockpit and a few preset goal buttons in VR are kept as fallbacks. The LLM can also just answer a question without proposing a plan.

Work:
1. `backend/ai/control/` (new):
   - `tools.py`: tool schemas derived from the robot contract:
     - `move(direction: forward|backward|left|right, seconds: 0.2–5, speed: 0–max)`
     - `turn(degrees: -180–180)`
     - `stop()`
     - `set_style(intensity, gait)` and `run_action(action_id)` limited to the Pi allowlist
     - `get_status()` and `get_detections()` (latest YOLO results)
   - `planner.py`: sends the goal plus current status and detections to the provider and gets back a list of tool calls. Every call is validated against the schemas; anything invalid fails the whole plan.
   - `executor.py`: runs the plan step by step through the `CommandGateway` as source `llm`. Read-only tools run immediately; movement steps go through the gateway's bounded moves and limits.
2. Provider support: add `plan_tool_calls()` to `LLMProvider`. Implement **Anthropic `claude-sonnet-5-5` first**, using the official `anthropic` SDK; `MockProvider` gets a deterministic keyword planner so tests and offline demos work. The other providers (`local`, `openrouter`, `openai`, Google) come later.
3. Flow: goal → plan → execute immediately (no confirm step). Any hand input, voice command, stop, or e-stop cancels the task. The gateway's limits apply to every step.
4. API: `POST /api/v1/llm/plan` (creates and starts the plan), `POST /api/v1/llm/task/cancel`, `GET /api/v1/llm/task`. WS messages `llm_plan`, `llm_step`, `llm_done`.
5. VR: plan panel (steps, current step, cancel).
6. Docs: update [ai_provider_setup.md](ai_provider_setup.md); its "LLM is advisory only and cannot issue robot movement commands" guarantee becomes "the LLM issues commands only through the gateway, within gateway limits, and never overrides stop or e-stop".

Done when:
- With the mock provider: a goal produces a validated plan; the plan starts without confirmation; hand input mid-plan cancels it; an out-of-range tool call rejects the plan.
- With `claude-sonnet-5-5` via the API: the same flow works on the simulator, then on the lifted robot.

## Phase 7: Robot camera for normal modes

**Goal:** the laptop uses the robot's camera, runs live detection, and serves the annotated feed and alerts to the Quest.

Work:
1. Camera source `PiStreamSource` in `backend/camera/`: reads the Pi MJPEG stream (`CAMERA_DEVICE=pi`, `CAMERA_PI_URL`, key from env). Reconnects on drop.
2. Live detection: run `AIDetectionService.process_frame` on sampled frames (e.g. 5 fps) in a worker, broadcast `detection` WS messages with bounding boxes, and keep the latest results for `get_detections()` (Phase 6). Needs a YOLO ONNX model at `YOLO_MODEL_PATH`; falls back to simulated detections when no model is present.
3. Quest: still reads video only from the laptop in normal modes; draws detection boxes over the camera screen.
4. Web cockpits: `/cockpit/vr` shows the real `/api/v1/camera/stream` instead of the placeholder canvas.

Done when: the Quest shows the robot's camera through the laptop with live boxes, and the Pi serves the laptop and an emergency-mode Quest at the same time without opening the camera twice.

---

## Cleanup (alongside the phases)

- Move `/cockpit` and `/cockpit/vr` from `/api/robot/command` and the legacy WS `robot_command` to the gateway-backed `/api/v1/*` (Phase 2–3), then remove the legacy `move(action, speed)` path from `RobotInterface`, `router.py`, and `/ws`.
- Optional, not scheduled: the Yahboom TCP compatibility transport from [06_IMPLEMENTATION_PROMPT.md](Barry_Robot_Documentation/06_IMPLEMENTATION_PROMPT.md).

## Decisions needed

| # | Question | Proposal |
|---|---|---|
| 1 | Emergency speed cap | 0.3 (half of `ROBOT_MAX_SPEED` 0.6) |
| 2 | Emergency e-stop input on the controllers | Both grips + STOP held |
| 3 | Entering emergency mode | Quest offers it after 3 s without laptop, operator confirms |
| 4 | Pi camera quality | 640×480 at 15 fps, tune on hardware |
| 5 | Local LLM and laptop GPU | **Decided 2026-11-10:** cloud LLM, Anthropic `claude-sonnet-5-5`; no laptop GPU needed. (Was: Ollama with a tool-calling model.) |
| 6 | LLM goal input | Voice first; typed goals in the laptop cockpit and VR presets as fallbacks |
| 7 | Speech-to-text model | **Decided 2026-11-10:** OpenAI transcription API (cloud). (Was: `faster-whisper` `small.en`, offline.) |
| 8 | Text-to-speech | Piper on the laptop, offline; reply text always shown in VR as well |
| 9 | Push-to-talk button | Left trigger held |
| 10 | Laptop camera vs robot camera in normal modes | Robot camera (Phase 7), laptop camera kept as a fallback setting |
