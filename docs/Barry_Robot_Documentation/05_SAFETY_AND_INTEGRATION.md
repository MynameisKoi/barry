# Barry Robot Integration and Safety Plan

## What is known

The uploaded DOGZILLA SDK performs synchronous serial I/O. Yahboom's joystick example reads a local Linux controller; its network application listens on TCP 6000 and maps remote packets to SDK calls. Camera and OLED helpers are separate utilities.

The screenshot does not show the content of Barry's existing Python modules. **Do not overwrite them without reading them.**

## Hardware-control ownership

Exactly one process should own the active serial controller. Protect SDK access using one serialized command dispatcher; avoid concurrent writes and reads from FastAPI handlers, joystick threads, or legacy Yahboom processes.

## Motion safety

1. Pi-side dead-man/watchdog: stop if commands cease, initially consider 300–500 ms and calibrate.
2. Prioritize stop; discard older queued motion after stop.
3. Require fresh operator session/lease and reject stale commands.
4. Clamp movement and posture to conservative tested ranges.
5. Use a joystick dead zone and reject malformed values.
6. Keep the selected intensity when stopping; reset only via explicit policy.
7. Ensure disconnection, process shutdown, and exceptions lead to safe stopping where possible.
8. Provide a separate physical emergency stop/power-disconnect procedure.
9. Avoid raw motor/leg/calibration APIs for normal remote operators.
10. Test with robot lifted/secured as appropriate and a clear operating area.

A successful HTTP response indicates command acceptance/dispatch, not proof of physical completion.

## Suggested rollout

**Stage 1 — Inspect existing code:** Review all eight Barry `backend/robot/` files; document current interfaces and tests.

**Stage 2 — Simulator:** Implement movement, posture, gait, action allowlist, telemetry state, and watchdog without hardware.

**Stage 3 — Local adapter:** Configure `DOGZILLALib` import on the Pi, test serial port and SDK compatibility, calibrate low/medium/high.

**Stage 4 — Pi server:** Add authenticated HTTP endpoints, serialized dispatcher, stop priority, and session ownership.

**Stage 5 — Laptop remote:** Use Barry HTTP by default; legacy Yahboom TCP only as a consciously selected compatibility mode.

**Stage 6 — Validation:** Test network loss, stale requests, rapid direction changes, repeated stop, posture/gait interactions, and telemetry errors.

## Out of scope

Changes to `backend/camera/`, `backend/auth/`, `backend/main.py`, frontend, and OLED/startup scripts. Those can be integrated later without putting their implementation into `backend/robot/`.
