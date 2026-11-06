# Prompt: Implement Barry's `backend/robot/` Only

You are a senior Python robotics engineer. Implement the **robot module only** for Barry using OOP, typed interfaces, composition, dependency injection, FastAPI, and conservative hardware safety.

## Existing files — preserve them

```text
backend/robot/
├── __init__.py
├── config.py
├── dogzilla.py
├── factory.py
├── interface.py
├── remote.py
├── server.py
└── simulator.py
```

**First inspect the actual contents** of these eight files. The supplied screenshot proves their names, not their existing classes or behavior. Do not overwrite working code or invent existing interfaces.

## Supplied source references

- `DOGZILLALib.py`: class `DOGZILLA`, serial SDK, movement/posture/style/action/telemetry methods.
- `joystick_dogzilla.py`: class `Dogzilla_Joystick`, local Linux `/dev/input/jsN` input mapping; not a network client.
- `app_dogzilla.py`: Yahboom TCP control server on port 6000, command parser, and camera endpoint.
- `camera_dogzilla.py`, `oled_dogzilla.py`, `compile.py`, `start_app.sh`, `kill_dogzilla.sh`: background references only; **do not implement camera, OLED, or deployment changes**.

Use the source code to verify behavior. Do not invent SDK methods or protocol fields.

## File responsibilities

- `interface.py`: hardware-independent robot interface covering movement, posture, movement style, actions, telemetry.
- `dogzilla.py`: local real SDK adapter, **composition** with `DOGZILLA`; import SDK lazily and configure its real location later on Pi.
- `simulator.py`: same interface with simulated state and telemetry; no Pi/serial dependencies.
- `remote.py`: same interface via network. Default to authenticated Barry HTTP API; optional Yahboom TCP compatibility transport. It is **not** a USB joystick driver.
- `factory.py`: choose `simulator`, `dogzilla`, or `remote`; for remote select `barry_http` or optional `yahboom_tcp`.
- `config.py`: robot mode, addresses, port, secrets, movement/posture presets, watchdog, timeout, allowlisted actions.
- `server.py`: Pi-side FastAPI with thin handlers, validated models, authenticated calls, safe dispatch.
- `__init__.py`: stable package exports if useful.

## Capabilities

### Movement
Forward, backward, strafe left/right, turn left/right, stop, and normalized joystick input (`forward`, `strafe`, `turn` in [-1,1]). Source methods: `forward`, `back`, `left`, `right`, `turnleft`, `turnright`, `move`, `turn`, `stop`. Use Yahboom joystick scaling as reference, but validate against real hardware. Implement dead zone and conservative limits.

### Intensity and style
Low→`pace('slow')`, medium→`pace('normal')`, high→`pace('high')`. Support `gait_type('trot'|'walk'|'high_walk')`. Track selected intensity separately from movement. **Stop must preserve intensity.** Document that Yahboom's examples sometimes change height when selecting slow pace; do not silently duplicate that coupling.

### Posture
Height low/medium/high, body translation X/Y/Z, body attitude roll/pitch/yaw, neutral posture. Use `translation()` and `attitude()` with calibrated safe presets. Do not confuse `reset()` with a posture-only reset.

### Actions
Use `action(action_id)` with an explicit allowlist. Source comments identify action 10 (three-axis), 11 (pee), 16 (swing); verify on target hardware. Support reset as an explicit separate operation. Exclude custom raw motor sequences.

### Telemetry
Battery, firmware version, roll, pitch, yaw via `read_battery`, `read_version`, `read_roll`, `read_pitch`, `read_yaw`. Include application-tracked command/intensity/gait and connection-health estimates, clearly labeled as such. Handle failed reads.

### Optional / restricted
Periodic rotation/translation, mark-time, cyclic `perform`, and IMU changes are not part of the ordinary Quest controls initially. Exclude public motor, leg, motor-load, and calibration operations.

## Proposed Pi HTTP API

- `POST /api/v1/motion`
- `POST /api/v1/stop`
- `POST /api/v1/posture`
- `POST /api/v1/movement-style`
- `GET /api/v1/actions`
- `POST /api/v1/actions/{action_id}`
- `POST /api/v1/reset`
- `GET /api/v1/telemetry`
- `GET /api/v1/status`

Use Pydantic request/response models. Preserve existing routes when possible; propose migration if routes already exist.

## Remote compatibility

The Yahboom reference TCP server listens on 6000 and dispatches commands such as `11` (joystick), `12` (direction/stop), `14` (pace), `21` (posture), `22` (height), `31` (action). Implement its frame format only after verifying all source-level encoding and parsing details. Handle TCP stream framing and errors. Never treat the legacy protocol as authenticated or inherently safe.

## Safety

Pi-side command watchdog, stop priority, stale-command rejection, one active operator, authenticated remote calls, serialized SDK access, conservative limits, safe disconnection, and physical emergency-stop guidance. A completed HTTP call does not prove motion completed.

## Tests

Test interface conformance, simulation, normalized joystick mapping, stop retaining intensity, gait/posture constraints, action allowlist, telemetry failures, network errors, stale commands, concurrent commands, and watchdog.

Keep changes inside `backend/robot/`. Propose external test files separately without changing them unless authorized.

## Workflow

1. Read the eight existing Barry robot files and the supplied Yahboom references.
2. Report existing behavior and gaps, citing exact code locations.
3. Propose minimal file-by-file changes; distinguish verified facts from assumptions.
4. **Wait for approval before rewriting files.**
5. Implement simulator/interface first, then DOGZILLA adapter, remote transport, Pi server, safety, and tests.
6. Explain how to select simulator locally, remote on laptop, and real DOGZILLA on Pi.

Do not create a new project or change `backend/main.py`, camera, auth, frontend, or unrelated files.
