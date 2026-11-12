# VR Joystick vs. 2D WASD Audit & Flipper Zero Mission Lifecycle Guide

Everything is implemented, verified with unit and live harness proofs, and merged to `main` via **PR [#121](https://github.com/MynameisKoi/barry/pull/121)** (`dfae684`).

---

## 1. Why the VR Joystick Didn't Move Dogzilla While 2D WASD Did (Root-Cause Audit)

Tracing a forward command from the 2D Cockpit (`/cockpit`) vs. the VR Joystick (`Barry.exe` / `/cockpit/vr`) revealed **three blockers** in the VR Joystick path that the 2D WASD buttons bypassed:

| Layer | 2D WASD (`POST /api/robot/command`) | VR Joystick (`POST /api/v1/motion`, `source="hand"`) | What Was Blocking the Joystick & How We Fixed It |
| :--- | :--- | :--- | :--- |
| **1. Active Mission Gate** | Calls [`create_robot_router`](../backend/robot/router.py#L43-L61) $\rightarrow$ `robot.move()` **directly**, bypassing `CommandGateway`. Never checks `mission_check()`. | Goes through [`CommandGateway.motion()`](../backend/gateway/gateway.py#L67-L94), which previously checked `if not self.mission_check(): return MISSION_REQUIRED` (HTTP `403`) for **all** sources (`hand`, `voice`, `llm`). | Added [`CommandGateway._mission_required(source)`](../backend/gateway/gateway.py#L63-L65) so **only `source in {"voice", "llm"}` requires an active mission**. `source="hand"` now moves Dogzilla immediately without an active mission, matching WASD. |
| **2. WebXR Client-Side Pre-Block** | Sends `POST /api/robot/command` immediately on button press. | In [`cockpit_vr.html`](../backend/static/cockpit_vr.html#L750-L759), `state.motionMissionBlocked = !state.mission.active` blocked `sendMotionAxes()` client-side before even sending HTTP requests. | Set `state.motionMissionBlocked = false` in [`applyMissionStatus()`](../backend/static/cockpit_vr.html#L750-L759) so WebXR hand/joystick controls never block client-side when idle. |
| **3. E-Stop & Link Recovery Sync** | `POST /api/robot/reset-emergency` reset `robot._emergency_stopped` directly without clearing `gateway._emergency_stopped`. | `CommandGateway` stayed latched in `ESTOP_ACTIVE` (`409`) or `online=False` (`503`) if reset via the legacy route or after a transient link blip. | Synced `/api/robot/reset-emergency` in [`cancel_llm_for_legacy_hand_controls`](../backend/main.py#L346-L351) to clear `gateway._emergency_stopped = False`, and updated [`CommandGateway._prepare()`](../backend/gateway/gateway.py#L340-L348) to auto-recover `self.online = True` when `self.robot.connected` is `True`. |

---

## 2. Mission Check & Flipper Zero Audit + Full Lifecycle Setup

### What We Found in the Audit
1. **Badge Seeding (`OP-004` / `2E:B7:76`)**: Your Flipper Zero 125 kHz HID H10301 badge (`2E:B7:76`, `OP-004` — *Primary Field Lead*) existed in the local `barry.db` and `cockpit.html`, but wasn't seeded in [`DatabaseManager.init_db()`](../backend/database.py#L167-L208). It is now permanently seeded with `INSERT OR IGNORE` in every database instance.
2. **USB Scan Callback (`on_flipper_tag_scanned`) Previously Only Checked Login**: Previously, [`on_flipper_tag_scanned()`](../backend/main.py#L209-L242) only verified the UID and broadcast `auth_event` — it did **not** check mission state, start/receive a mission, or finish an active mission.
3. **Full Flipper Zero Workflow Now Wired ([`execute_flipper_credential_scan`](../backend/main.py#L148-L206))**:
   - **Tap #1 (Login + Mission Check $\rightarrow$ Receive/Start Mission)**:
     - Verifies your Flipper Zero badge UID (`2E:B7:76` / `2E B7 76` / `2EB776`, or any registered NFC/RFID UID) against SQLite `operators` and logs the scan in `authorization_logs`.
     - Checks `mission_service.get_status()`. Since no mission is active (`active == False`), it **automatically receives and starts a new mission** (`status="in_progress"`, `zone="Sector-A"`) for that operator, **unlocking Voice (`/api/v1/voice/command`) and LLM (`/api/v1/llm/plan`) control**.
     - Broadcasts both `auth_event` and `mission_update` over WebSocket `/ws` to update both the 2D Cockpit (`/cockpit`) and 3D WebXR Cockpit (`/cockpit/vr`).
   - **Tap #2 (Login + Mission Check $\rightarrow$ Finish Mission)**:
     - When a mission is already active (`active == True`), scanning your badge again **automatically concludes/finishes the active mission** (`status="completed"`), dispatches a safety `gateway.stop(source="hand")` to halt the robot, and **re-locks Voice and LLM control** (`MISSION_REQUIRED`), while keeping WASD and VR Hand Joystick available.
   - **API & Hardware Status Inspection**:
     - `GET /api/auth/flipper/status` ([`get_flipper_status`](../backend/main.py#L517-L530)) returns real-time USB serial detection (`usb_port_detected`, `serial_connected`, `listener_running`, `scan_mode`, `last_scanned_uid`) plus the current `mission` object.
     - `POST /api/auth/scan` ([`scan_nfc_credential`](../backend/main.py#L481-L514)) now accepts optional `"mission_action": "auto" | "start" | "stop" | "none"` and `"zone": "Sector-A"`, so you can also trigger or test the exact same Flipper workflow from scripts or UI buttons.

---

## 3. Proofs of Execution

### A. Live End-to-End Proof (`flipper_mission_and_motion_proof.py`)
```text
==============================================================================
STEP 1: NO ACTIVE MISSION — TEST WASD, HAND JOYSTICK, VOICE, AND LLM
==============================================================================
Mission active? False (status=completed)
[WASD /api/robot/command] ->  | Pi wire calls: ['/api/v1/session', '/api/v1/motion']
[VR Joystick /api/v1/motion (source='hand')] -> ACCEPTED | Pi wire calls: ['/api/v1/motion']
[Voice (source='voice') without mission] -> MISSION_REQUIRED (An active mission is required for motion.)
[LLM   (source='llm')   without mission] -> MISSION_REQUIRED (An active mission is required for motion.)

==============================================================================
STEP 2: FLIPPER ZERO BADGE SCAN #1 (2E:B7:76) -> LOGIN + RECEIVE/START MISSION
==============================================================================
Flipper Scan #1 Authorized : True
Operator                   : Primary Field Lead (OP-004) [2E:B7:76]
Mission Action Taken       : started
Mission State              : active=True, id=MSN-F104AAF4, zone=Sector-A

==============================================================================
STEP 3: MISSION ACTIVE — VOICE & LLM NOW UNLOCKED AND MOVE DOGZILLA
==============================================================================
[Voice (source='voice') WITH mission] -> ACCEPTED | Pi wire calls: ['/api/v1/motion', '/api/v1/stop']
[LLM   (source='llm')   WITH mission] -> ACCEPTED | Pi wire calls: ['/api/v1/motion', '/api/v1/stop']

==============================================================================
STEP 4: FLIPPER ZERO BADGE SCAN #2 (2E:B7:76) -> FINISH MISSION & RE-LOCK AI/VOICE
==============================================================================
Flipper Scan #2 Authorized : True
Mission Action Taken       : stopped
Mission State              : active=False, status=completed
[Voice after mission finished]       -> MISSION_REQUIRED
[LLM   after mission finished]       -> MISSION_REQUIRED
[VR Joystick after mission finished] -> ACCEPTED | Pi wire calls: ['/api/v1/motion']

ALL PROOFS PASSED SUCCESSFULLY!
```

### B. Pytest Suite Proof (`83 passed`)
All 83 tests across [`tests/test_gateway.py`](../tests/test_gateway.py), [`tests/test_gateway_relay_integration.py`](../tests/test_gateway_relay_integration.py), [`tests/test_gateway_wiring.py`](../tests/test_gateway_wiring.py), [`tests/test_auth.py`](../tests/test_auth.py), [`tests/test_missions.py`](../tests/test_missions.py), and [`tests/test_mission.py`](../tests/test_mission.py) pass cleanly (`83 passed in 13.32s`).

---

## 4. Using Your Physical Flipper Zero With the Backend

1. Plug your Flipper Zero into your laptop via USB (make sure qFlipper is closed so it doesn't lock the serial port; if running inside a VirtualBox VM, attach **Devices $\rightarrow$ USB $\rightarrow$ Flipper Devices Inc. Flipper Zero** so `/dev/ttyACM0` appears inside Linux).
2. [`FlipperListener`](../backend/auth/flipper_listener.py#L87-L112) polls every 2 seconds, automatically connects to `/dev/ttyACM0`, and sends `rfid read` to engage the 125 kHz RFID reader on your Flipper Zero (or you can emulate/read `2E:B7:76`).
3. You can verify the live connection anytime with:
   ```bash
   curl -s http://localhost:8000/api/auth/flipper/status | python3 -m json.tool
   ```
   Or trigger a simulated Flipper badge tap from the terminal with:
   ```bash
   curl -s -X POST http://localhost:8000/api/auth/scan \
     -H "Content-Type: application/json" \
     -d '{"tag_uid": "2E:B7:76", "mission_action": "auto", "zone": "Sector-A"}' | python3 -m json.tool
   ```
