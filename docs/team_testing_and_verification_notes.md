# Team Testing, Hardware Verification & Shared Operational Notes

> [!NOTE]
> This shared document serves as the operational guide and physical test checklist for both developers and operators testing **BARRY** on physical **Dogzilla quadruped robot** hardware and the **Meta Quest 3S VR headset**.
>
> All backend features, gateway safety interlocks, and WebXR cockpit layouts are verified with passing tests in the repository. The items below require hands-on physical validation on the live robot and headset.

---

## 1. Quick Remote Network Configuration

Because the Dogzilla robot and Quest headset are connected via a remote network, ensure the environment variables and network routes are configured as follows:

| Component | Target URL / Port | Environment Variable / Setting |
| :--- | :--- | :--- |
| **Dogzilla Robot API** | `http://<dogzilla-ip>:8100` | `ROBOT_MODE=remote`<br>`ROBOT_REMOTE_URL=http://<dogzilla-ip>:8100` |
| **Dogzilla Camera MJPEG** | `http://<dogzilla-ip>:6500/mjpeg` (or `/video_feed`) | `CAMERA_SOURCE=dog`<br>`DOG_VIDEO_URL=http://<dogzilla-ip>:6500/mjpeg` |
| **Laptop Backend Service** | `http://<laptop-ip>:8000` | Service active: `systemctl --user status barry-backend.service` |
| **WebXR Cockpit URL** | `http://<laptop-ip>:8000/cockpit/vr` | Open in Meta Quest Browser |
| **Client Remote Logs** | `http://<laptop-ip>:8000/api/debug/client-log` | Remote JavaScript logs from Quest headset (no USB cable needed) |

---

## 2. Hardware Verification Checklist: Dogzilla Quadruped

The following physical behaviors must be observed and validated on the physical quadruped:

### A. Posture Pitch Direction & Camera View Calibration
- [ ] **Physical Pitch Sign Verification**:
  - The gateway issues pitch commands in degrees via `/api/v1/posture`.
  - **Check**: When pitch is commanded **positive (+)** vs. **negative (-)**, verify which sign tilts the robot's nose and camera **downward** toward the ground vs. **upward**.
  - **Why this matters**: In `backend/gateway/gateway.py`, the attitude sign convention needs physical hardware verification to calibrate autonomous find-object search sweeps (`capture_look`).
- [ ] **Hardware Settling Time (`POSTURE_SETTLE_S`)**:
  - Default settling delay is `0.5s` to allow leg servos to complete stance repositioning before gait translation resumes.
  - **Check**: Does the robot reach a stable stance within 0.5s, or does it require more/less settling delay?

### B. Body Height Stance & Locomotion (PR #150 & PR #151)
- [ ] **LOW Body Height Clearance**:
  - On the WebXR **BODY HEIGHT** panel or `/api/v1/posture`, set height to **LOW**.
  - **Check**: Verify that the robot chassis clears the floor and does not drag its underbelly or overheat leg servos while walking.
- [ ] **HIGH Body Height Stability**:
  - Set height to **HIGH** and command forward/turn motion with the left/right thumbsticks.
  - **Check**: Ensure the robot does not wobble or lose balance when walking at maximum stance height.
- [ ] **Auto-Leveling Before Motion**:
  - Tilt the robot body (or issue a pitch command), then push the left joystick to move forward.
  - **Check**: Verify the robot pauses for 0.5s to restore level pitch/roll attitude while preserving the selected body height, then proceeds forward smoothly.

### C. Locomotion Intensity Fallback (PR #139)
- [ ] **Speed Buttons (LOW / MEDIUM / HIGH)**:
  - Physical Dogzilla API `:8100` returns 404 for `/api/v1/movement-style`.
  - The laptop backend catches this and performs laptop-side velocity scaling (`LOW: 0.4`, `MEDIUM: 0.7`, `HIGH: 1.0`).
  - **Check**: Press **LOW**, **MEDIUM**, and **HIGH** on the VR control strip or 2D page and confirm the robot visibly speeds up and slows down proportionally.

---

## 3. WebXR Headset Verification Checklist: Meta Quest 3S

Open `http://<laptop-ip>:8000/cockpit/vr` in the Quest Browser and click **ENTER VR**:

### A. Immersive Cockpit Layout & Alignment (PR #126, #127, #151)
- [ ] **1:1 Cylindrical Arc Layout**:
  - Center: 1.8m camera viewport (`Z = 2.0m`).
  - Left Arc (-36°): Status panel, and directly below it (`EYE - 0.51m`), the **BODY HEIGHT** panel.
  - Right Arc (+36°): Actions panel (`Stand`, `Sit`, `Stretch`, `High Walk`, etc.).
  - Far Right Arc (+46°): Brain AI inspection panel.
  - Bottom (-35° tilt): Control strip positioned comfortably below line of sight.
- [ ] **Movable Panels & Safety Lockout**:
  - Point at any panel, hold the controller grip button, and move your hand to reposition the panel in 3D space.
  - **Check Safety Interlock**: While grabbing and moving a panel, thumbstick movement inputs must be physically locked to neutral (driving is disabled during panel adjustment).
  - Double-click both thumbsticks together (or press desktop "Reset panel layout") to restore factory layout coordinates.

### B. Drive & Control Inputs
- [ ] **Thumbstick Driving (Bypassed Mission)**:
  - Left thumbstick: Forward/Backward and Strafe Left/Right.
  - Right thumbstick: Yaw Turn Left/Right.
  - **Check**: Robot moves immediately without requiring an RFID mission badge (`REQUIRE_ACTIVE_MISSION=false`).
- [ ] **Need-Center Safety Interlock**:
  - After triggering a stop or release, thumbsticks must return to the center deadzone (<0.1) before any new motion command is dispatched.
- [ ] **Emergency Stop (E-Stop)**:
  - Press button **A** on the right controller (or hold Grips + **A** for 0.3s).
  - **Check**: Big red emergency banner appears across the viewport, latches immediately, and halts all motion until cleared.
- [ ] **VR Controller Haptic Feedback**:
  - Touch any button or grab a movable panel: feel a crisp tactile click (25ms) in the hand controller.
  - Latch Emergency Stop (button **A** or grips+**A**): feel a heavy, unmistakable warning rumble (300ms at 1.0 intensity) across both controllers.
  - Push-to-talk (hold **Y**): feel subtle confirmation pulses on record start and release.
  - Autonomous find locks onto target (`WATCHING`): feel double-pulse confirmation that the AI spotted the target.

### C. Voice & Push-to-Talk (PR #128, PR #132)
- [ ] **In-Headset Push-to-Talk**:
  - **Option 1**: Hold controller button **Y** for ≥ 0.35s (tap < 0.35s toggles Brain panel).
  - **Option 2**: Point controller ray at the red **Hold to talk** button on the control strip and pull trigger.
  - **Feedback**: Header chip shows `LISTENING · release to send`; speak command ("forward 1 meter", "turn left", "emergency stop"); release button; header displays `TRANSCRIBING…` and executes intent.
- [ ] **Live Mic Volume Meter & VU Graphic**:
  - While speaking during push-to-talk, verify the Header chip animates a live volume bar glyph (`LISTENING ▅`) and the "Hold to talk" button displays a dynamic emerald green level meter (`#34D399`) that rises and falls with your voice volume.

### D. AI Vision & Live Detections Overlay (PR #130, #131)
- [ ] **YOLO Overlays on Camera Viewport**:
  - Verify glowing neon bounding boxes with label pills (e.g. `person 89%`, `chair 92%`) render over the live camera stream.
  - Header chip displays `DETECT · <n>`.
  - Confirm bounding boxes hide automatically if camera feed or detection telemetry drops for >2 seconds.

### E. Autonomous Find-Object Session Controls (PR #136, #138, #144, #152)
- [ ] **Find Presets in Brain Panel**:
  - Open Brain panel (press **Y** or point and click).
  - Click any preset: **ball**, **person**, **chair**, or **bottle**.
  - **Check**: Movable AUTO progress bar appears below camera showing current phase (`SEARCHING`, `WATCHING`, `LOST`) with countdown timer and step arguments.
  - **Check**: The dedicated **Stop finding** button cleanly interrupts the autonomous loop immediately.
- [ ] **Scrollable Brain Answers**:
  - When the AI generates a long multi-line explanation, verify the answer area expands downward and can be scrolled vertically using:
    - On-screen ▲ / ▼ touch buttons.
    - Right thumbstick vertical deflection (Y-axis).
    - Desktop mouse wheel.

---

## 4. Diagnostics & Troubleshooting

If unexpected behavior occurs during field tests:

1. **Headset JS Error Logs**:
   - Query `http://<laptop-ip>:8000/api/debug/client-log` from any browser on the network. All unhandled JavaScript exceptions, WebXR tracking losses, and network failures in the Quest headset are logged there with timestamps.
2. **Backend Service Logs**:
   ```bash
   journalctl --user -u barry-backend.service -n 100 --no-pager
   ```
3. **Hardware Gateway Status**:
   - Check `http://<laptop-ip>:8000/api/v1/status` to view real-time latency (`rtt_ms`), control session state, and commanded vs. telemetry posture.
