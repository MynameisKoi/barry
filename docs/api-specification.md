# BARRY API Specification (v1)

This document describes the HTTP routes that exist on `main`. The code is the source of truth. Where a shape is defined in a module, this page points there rather than copying every field. Routes marked **dry run** accept requests but do not send anything to the robot yet.

Where a route is defined is given in brackets after each heading.

---

## Services and base URLs

| Service | Base URL | Used by |
|---|---|---|
| Laptop backend | `http://<LAPTOP_IP>:8000` | Quest (`/api/v1/*`), cockpit browser |
| Pi Robot API | `http://<PI_ADDRESS>:8100` | Laptop relay only, plus emergency mode from the Quest (Phase 4, not built yet) |

The laptop backend is `backend.main:app`. The Pi API is `backend.robot.server:app`.

---

## Authentication

| Route group | Header | Secret | Missing or wrong key | Key not configured |
|---|---|---|---|---|
| Quest relay, voice, LLM (`/api/v1/session`, `/api/v1/motion`, `/api/v1/motion/discrete`, `/api/v1/emergency-stop/reset`, `/api/v1/posture`, `/api/v1/reset`, `/api/v1/movement-style`, `/api/v1/actions*`, `/api/v1/telemetry`, `/api/v1/status`, `/api/v1/voice/*`, `/api/v1/llm/*`) | `X-Robot-Key` | `QUEST_API_KEY` on the laptop | 401 | 503 (fails closed) |
| Camera (`/api/v1/camera/*`) | `X-Camera-Key` header, or `?token=` query parameter | `CAMERA_API_KEY` on the laptop | 401 | 503 (fails closed) |
| Pi Robot API (all routes except `/healthz`) | `X-Robot-Key` | `ROBOT_API_KEY`, shared by laptop and Pi | 401 | 401 on every authenticated route; in `dogzilla` mode the app fails on its first request with `ROBOT_API_KEY is required` (see `backend/robot/server.py`) |

**No Quest key required:** `POST /api/v1/stop` and `POST /api/v1/emergency-stop` are always allowed, even with a missing or wrong key, or with no `QUEST_API_KEY` set on the laptop. They can only make the robot safer. This is an explicit two-path allowlist (`backend/robot/relay_router.py`, `relay_auth`). Every other route stays keyed.

The laptop sends the Pi key to the Pi itself; the Quest's key is never forwarded (`backend/robot/relay.py`, `_request_headers`).

Auth dependencies: `backend/robot/relay_router.py` (`QuestRelayAuth`), `backend/camera/auth.py` (`CameraAuth`), `backend/robot/server.py` (`authenticate`).

---

## 1. Quest robot relay (laptop) [`backend/robot/relay_router.py`]

The laptop relays these routes to the Pi unchanged, so the Quest uses the same request and response shapes as the Pi API in section 2. Two extra behaviours apply:

- If the Pi cannot be reached, the laptop returns **503** with `{"detail": "Pi Robot API unavailable."}`.
- Responses from the Pi, including its 401, 409, 422, 423 and 503, are passed through with their status codes.

| Method | Path | Session header | Forwarded headers | Notes |
|---|---|---|---|---|
| POST | `/api/v1/session` | | `X-Control-Session` | Acquire or renew the control lease |
| DELETE | `/api/v1/session` | `X-Control-Session` | | Release the lease and stop |
| POST | `/api/v1/motion` | `X-Control-Session`, `X-Command-Seq`, `X-Command-Sent-At` | same | Analog joystick move; body in section 2.2 |
| POST | `/api/v1/motion/discrete` | `X-Control-Session`, `X-Command-Seq`, `X-Command-Sent-At` | same | One directional move; body in section 2.2 |
| POST | `/api/v1/stop` | **No Quest key required** (always allowed, can only make the robot safer) | none | Priority stop; no session or sequence. Uses the priority lane. |
| POST | `/api/v1/emergency-stop` | **No Quest key required** (always allowed, can only make the robot safer) | none | Latches the emergency stop. Uses the priority lane, like `/stop`. Audited on every request. |
| POST | `/api/v1/emergency-stop/reset` | `X-Control-Session`, `X-Command-Seq`, `X-Command-Sent-At` | same | Clears the latch. Audited. |
| POST | `/api/v1/posture` | `X-Control-Session`, `X-Command-Seq`, `X-Command-Sent-At` | same | Height and attitude; body in section 2.2 |
| POST | `/api/v1/reset` | `X-Control-Session`, `X-Command-Seq`, `X-Command-Sent-At` | same | Full robot reset. Audited. |
| POST | `/api/v1/movement-style` | `X-Control-Session`, `X-Command-Seq`, `X-Command-Sent-At` | same | Body in section 2.2 |
| GET | `/api/v1/actions` | none | none | Lists allowlisted preset actions |
| POST | `/api/v1/actions/{action_id}` | `X-Control-Session`, `X-Command-Seq`, `X-Command-Sent-At` | same | Preset action by ID |
| GET | `/api/v1/telemetry` | none | none | Measured telemetry |
| GET | `/api/v1/status` | none | none | Link, lease and watchdog state |

**Priority lane:** `/api/v1/stop` and `/api/v1/emergency-stop` are sent on a dedicated HTTP client (`PRIORITY_PATHS` in `backend/robot/relay_router.py`), so they are never queued behind an in-flight motion request. Emergency stop is audited on every request. Its reset goes through the normal lane with session and sequence checks.

Relay settings come from `backend/robot/config.py` (`RobotConfig.from_env`): `ROBOT_MODE=remote`, `ROBOT_REMOTE_URL`, `ROBOT_REMOTE_TIMEOUT`, `ROBOT_API_KEY`.

---

## 2. Pi Robot API [`backend/robot/server.py`, `backend/robot/protocol.py`]

Runs on the Pi as `backend.robot.server:app`. Paths and header names come from `backend/robot/protocol.py`, which the laptop also imports. Request models are in `backend/robot/server.py` (`MotionRequest`, `DiscreteMotionRequest`, `MovementStyleRequest`, `PostureRequest`) and `backend/robot/schemas.py`.

### Headers

| Header | Meaning |
|---|---|
| `X-Robot-Key` | Required on every route except `/healthz`. Equal to `ROBOT_API_KEY`. |
| `X-Control-Session` | The session ID from `POST /api/v1/session`. Required on mutating routes except stop and emergency stop. |
| `X-Command-Seq` | Strictly increasing integer per session. `seq <= last accepted` returns 409. |
| `X-Command-Sent-At` | Sender epoch milliseconds. Checked only when `ROBOT_MAX_COMMAND_AGE_MS` is greater than 0. |

### 2.1 Routes

| Method | Path | Auth | Session | Description |
|---|---|---|---|---|
| GET | `/healthz` | No | No | Returns `{"status": "ok", "connected": bool}` |
| POST | `/api/v1/session` | Yes | No (renews if the header matches) | Returns `{"session_id": str, "ttl_s": float}`; 409 if another session holds the lease |
| DELETE | `/api/v1/session` | Yes | Yes | Releases the lease and dispatches a stop |
| POST | `/api/v1/motion` | Yes | Yes + seq | Analog move |
| POST | `/api/v1/motion/discrete` | Yes | Yes + seq | One allowlisted directional move |
| POST | `/api/v1/stop` | Yes | No | Priority stop |
| POST | `/api/v1/emergency-stop` | Yes | No | Latches the emergency stop |
| POST | `/api/v1/emergency-stop/reset` | Yes | Yes + seq | Clears the latch |
| POST | `/api/v1/movement-style` | Yes | Yes + seq | Intensity and gait |
| POST | `/api/v1/posture` | Yes | Yes + seq | Height preset and attitude |
| GET | `/api/v1/actions` | Yes | No | Lists safe preset actions |
| POST | `/api/v1/actions/{action_id}` | Yes | Yes + seq | Runs a preset action (422 if not allowlisted) |
| POST | `/api/v1/reset` | Yes | Yes + seq | Full robot reset |
| GET | `/api/v1/telemetry` | Yes | No | Measured telemetry |
| GET | `/api/v1/status` | Yes | No | Link, lease, watchdog and intensity or gait |

### 2.2 Request bodies

- `POST /motion`: `{"forward": float, "strafe": float, "turn": float}`. Each is in `[-1, 1]`, defaults to 0, and extra fields are rejected.
- `POST /motion/discrete`: `{"direction": "forward" | "backward" | "left" | "right" | "turn_left" | "turn_right", "intensity": "low" | "medium" | "high" (optional)}`.
- `POST /movement-style`: `{"intensity": "low" | "medium" | "high", "gait": "trot" | "walk" | "high_walk"}`.
- `POST /posture`: `{"height": "low" | "medium" | "high", "roll": float, "pitch": float, "yaw": float}`. Attitude limits are in `backend/robot/config.py`.

### 2.3 Responses

Commands return a `CommandResult` (`backend/robot/interface.py`, `to_dict()`):

```json
{"status": "accepted", "command": "motion", "message": "...", "intensity": "medium"}
```

`status` is one of `accepted`, `stop_dispatched`, `rejected`, `failed`. These describe API acceptance only, not physical confirmation.

`GET /api/v1/status` returns the fields built in `backend/robot/server.py`: `robot_mode`, `connected`, `control_session_active`, `last_command_age_ms`, `watchdog` (`armed`, `timeout_ms`), `intensity`, `gait`.

`GET /api/v1/telemetry` returns `Telemetry.to_dict()` (`backend/robot/interface.py`): `battery`, `orientation`, `firmware`, `read_errors`, `control_state`.

### 2.4 Error codes

| Code | Meaning (from `backend/robot/protocol.py`) |
|---|---|
| 401 | Missing or wrong `X-Robot-Key` |
| 409 | Session held by someone else, wrong or missing session, stale sequence, command invalidated by a stop |
| 422 | Validation failure, unsafe combination, or unknown action ID |
| 423 | Emergency stop is latched |
| 503 | Robot disconnected, or command failed |

---

## 3. Camera [`backend/camera/router.py`]

Prefix `/api/v1/camera`. Every route needs the camera key (`X-Camera-Key` or `?token=`, see Authentication).

| Method | Path | Response |
|---|---|---|
| GET | `/api/v1/camera/status` | `CameraStatus` (`backend/camera/status.py`). Always 200; a disconnected camera is a valid status. |
| GET | `/api/v1/camera/snapshot` | Latest JPEG (`image/jpeg`). 503 if no fresh frame. |
| GET | `/api/v1/camera/stream` | MJPEG (`multipart/x-mixed-replace; boundary=frame`). 429 when the viewer cap is reached, 503 if unavailable. |

All camera responses carry `Cache-Control: no-store`.

---

## 4. Voice prototype (dry run) [`backend/voice/router.py`]

### `POST /api/v1/voice/command`

Auth: `X-Robot-Key` (`QUEST_API_KEY`).

- Request: `multipart/form-data` with one file field named `audio`. Clips up to 15 s; the upload is capped at 3,000,000 bytes.
- Response (200):

```json
{"transcript": "forward two seconds", "command": "...", "args": {...}, "matched": true}
```

`command`, `args` and `matched` come from `match_command()` in `backend/voice/commands.py`. Nothing is sent to the robot yet (dry run).

Errors: 400 empty clip; 413 clip too large; 502 transcription failed; 503 transcription not configured (`OPENAI_API_KEY` unset). Transcription uses the OpenAI transcription API; the model is `OPENAI_TRANSCRIBE_MODEL` (default `gpt-transcribe`), see `backend/voice/transcribe.py`.

---

## 5. LLM control prototype (dry run) [`backend/ai/control/router.py`]

Auth: `X-Robot-Key` (`QUEST_API_KEY`). The brain is Anthropic `claude-sonnet-5-5` by default (`ANTHROPIC_CONTROL_MODEL` overrides it; `ANTHROPIC_API_KEY` is required). `LLM_PLANNER=mock` selects the offline brain. See `backend/ai/control/router.py`, `brain.py` and `executor.py`.

The brain is a loop. The model calls robot tools one step at a time and sees each result before it picks the next. There is **no confirm step**: the loop starts as soon as you post a goal. The executor runs against a dry-run gateway until the command gateway (#57) is merged.

### `POST /api/v1/llm/plan`

Starts the brain loop and **returns at once**. Poll `GET /api/v1/llm/task` for the result.

- Request: `{"goal": str}` (1–500 characters).
- Response (200): `{"status": "started", "task": {...}}`, with the task in state `running`.
- Errors: 503 LLM control not configured (`ANTHROPIC_API_KEY` unset). The planner is not called, so no 422 or 502 is returned. Failures during the run appear as task state (below).

### `GET /api/v1/llm/task`

Returns the current task, or `{"state": "idle"}` if none has run. Poll this until `state` is no longer `running`.

Task fields (`Task.to_dict()` in `backend/ai/control/executor.py`):

| Field | Meaning |
|---|---|
| `id`, `goal` | Task ID and the goal text |
| `state` | `running`, `done`, `cancelled`, `failed`, `limit` or `refused` |
| `answer` | The model's final text, when the task is `done` |
| `turns` | Model turns used so far |
| `steps` | Each tool call: `tool`, `args`, `state` (`running`, `done`, `rejected`, `failed`, `skipped`), `result`, `error` |
| `message` | Why the task ended: the limit hit, an error, or a refusal. `null` on success. |

**Limits.** A task stops and calls `stop()` on the robot when it hits one of these caps. It then ends in state `limit`, with the reason in `message`:

| Env var | Default | Cap |
|---|---|---|
| `LLM_MAX_STEPS` | `12` | Tool calls in one task |
| `LLM_MAX_TURNS` | `8` | Model turns in one task |
| `LLM_TIMEOUT_S` | `60` | Wall-clock seconds for the task |

**Failure states.** These show up in `state`, not as HTTP errors:
- `failed`: the model output was cut off, or another error occurred. `message` says which.
- `refused`: the model declined the request. `message` is "The assistant declined this request."
- `cancelled`: `POST /api/v1/llm/task/cancel` was called. Any running step is marked `skipped`.

### `POST /api/v1/llm/task/cancel`

- Response: `{"cancelled": bool, "task": {...}}`. `cancelled` is `false` if no task is running.

---

## 6. Other laptop routes (not `/api/v1`)

These predate v1. Shapes are defined in the code, so this page does not repeat them. Check the module before relying on one.

| Route | Source |
|---|---|
| `GET /` | `backend/main.py` |
| `GET /cockpit`, `GET /cockpit/vr` (browser cockpits) | `backend/main.py` |
| `GET /api/camera/stream` (legacy camera stream, no key) | `backend/main.py` |
| `POST /api/auth/scan`, `GET /api/operators`, `GET /api/auth/logs` | `backend/main.py`, `backend/api/auth.py` |
| `POST /api/mission/start`, `POST /api/mission/stop`, `GET /api/mission/status` | `backend/main.py` |
| `GET /api/missions`, `GET /api/missions/{mission_id}` | `backend/api/missions.py` |
| `POST /api/events/ingest`, `GET /api/events`, `GET /api/events/structured`, `GET /api/events/{event_id}` | `backend/main.py` |
| `POST /api/ai/analyze`, `POST /api/ai/report/{mission_id}`, `GET /api/ai/status`, `GET /api/ai/call-logs`, `GET /api/ai/analyses`, `POST /api/ai/trigger-detection` | `backend/main.py` |
| `/api/robot/status`, `/api/robot/command`, `/api/robot/reset-emergency`, `/api/robot/stop`, `/api/robot/posture` (legacy command path) | `backend/robot/router.py` |
| `/ws` (WebSocket) | `backend/main.py` |

`backend/api/events.py`, `backend/api/intelligence.py` and `backend/api/robot.py` define routers that `backend/main.py` does not mount. Do not document them as live routes.

The legacy `/api/robot/*` command path is planned for removal once the cockpit uses the gateway-backed `/api/v1/*` routes.
