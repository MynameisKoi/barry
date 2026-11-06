# Barry Robot Module Architecture

## Existing directory (from screenshot)

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

The screenshot establishes file names only, not class implementations. Inspect these files before editing.

## Proposed responsibilities

| File | Responsibility |
|---|---|
| `interface.py` | Hardware- and transport-independent robot contract |
| `dogzilla.py` | Real adapter; contains a `DOGZILLA` SDK object |
| `simulator.py` | Same contract, fake hardware and explicitly simulated telemetry |
| `remote.py` | Same contract, network client; **not** the Yahboom USB joystick driver |
| `factory.py` | Select simulator, local DOGZILLA, or remote implementation |
| `config.py` | Mode, transport, limits, timeouts, credentials, presets |
| `server.py` | Pi-side authenticated API, request validation, dispatch |
| `__init__.py` | Package initialization / stable exports as appropriate |

## OOP design

Use **abstraction** for the common robot interface; **composition** in `dogzilla.py` to contain `DOGZILLA`; **polymorphism** for local, remote, and simulated implementations; and **dependency injection** to test without hardware. Do not inherit from the Yahboom SDK solely to expose its methods.

## Deployment

```text
Quest controller → Barry laptop backend → remote.py
                                          |
                              authenticated Barry HTTP
                                          |
                              Pi: server.py → dogzilla.py
                                               |
                                          DOGZILLALib
                                               |
                                           serial robot
```

Development: `factory.py` selects `simulator.py`. Laptop-to-Pi: selects `remote.py`. Pi: selects `dogzilla.py`.

The Yahboom legacy TCP server in `app_dogzilla.py` is an **optional compatibility alternative** to Barry's own Pi API, not a second concurrent serial controller. It listens on TCP 6000 in the uploaded source.

## API surface (proposed, not already implemented)

| Category | Operations |
|---|---|
| Movement | forward, backward, strafe left/right, turn left/right, stop, normalized joystick |
| Posture | body height presets, translation, roll/pitch/yaw, neutral |
| Movement style | low/medium/high intensity, pace, gait |
| Actions | allowlisted preset actions, reset |
| Telemetry | battery, version, roll/pitch/yaw, connection and commanded state |

Suggested Pi endpoints: `POST /api/v1/motion`, `/api/v1/stop`, `/api/v1/posture`, `/api/v1/movement-style`, `/api/v1/actions/{action_id}`, `/api/v1/reset`; `GET /api/v1/actions`, `/api/v1/telemetry`, `/api/v1/status`.

**Stop preserves intensity:** maintain selected intensity separately; do not reset it in the stop handler.

## Exclusions

Camera streaming belongs to `backend/camera/`. OLED display and installation scripts are not part of the robot control API. Direct servo/leg/calibration commands are maintenance-only.
