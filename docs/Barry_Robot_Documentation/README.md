# Barry Robot Module — Documentation

This documentation set is based on the uploaded Yahboom source files and the `backend/robot/` directory shown in the screenshots. The actual contents of Barry's eight Python modules were **not** uploaded; their responsibilities below are proposed, not verified existing behavior.

## Documents

1. [Architecture](01_ARCHITECTURE.md) — OOP structure and deployment modes
2. [DOGZILLA SDK Reference](02_DOGZILLA_SDK_REFERENCE.md) — hardware methods and behavior
3. [Joystick Mapping](03_JOYSTICK_MAPPING.md) — Yahboom controller inputs and Barry equivalents
4. [Remote Protocol](04_REMOTE_PROTOCOL.md) — existing Yahboom TCP protocol and limitations
5. [Safety and Integration](05_SAFETY_AND_INTEGRATION.md) — constraints and implementation order
6. [Implementation Prompt](06_IMPLEMENTATION_PROMPT.md) — ready-to-paste coding prompt

## Supplied sources

- `DOGZILLALib.py` — robot hardware SDK
- `joystick_dogzilla.py` — Linux USB joystick controller
- `app_dogzilla.py` — network control application
- `camera_dogzilla.py` — OpenCV camera class (out of robot-module scope)
- `oled_dogzilla.py` — OLED diagnostics (out of scope)
- `compile.py`, `start_app.sh`, `kill_dogzilla.sh` — deployment utilities (out of scope)
- Two screenshots — existing Barry folder structure

**Scope:** Only `backend/robot/`; camera, auth, and frontend changes are not part of this specification.
