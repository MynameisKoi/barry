# Multi-Agent Collaboration Protocol: BARRY Project

This document establishes the communication, pull request (PR), and synchronization protocol between the two autonomous coding agents collaborating on the BARRY repository:

- **Agent Alpha (Cockpit, Gateway, Backend, WebXR, Linux VM)**
- **Agent Beta (Dogzilla Hardware, Unity PC-VR / Barry.exe, Windows / VR Rig)**

---

## 1. Agent Communication Channel & Rules

### A. Pull Request Reviews & Comments
Every Pull Request created or updated by either agent MUST include:
1. **Clear Context & Motivation**: Exactly what problem is solved and why.
2. **Component Changes**: Breakdown of backend, API, frontend/Unity, or WebXR changes.
3. **Contract / Wire Changes**: Any change to endpoints, headers, request/response models, or defaults.
4. **Verification Proofs**: Terminal output, unit test results, or screenshots/logs proving it works.
5. **Impact on Partner Agent**: Explicit instructions on what the other agent needs to do (e.g. `git pull`, restart service, env variable changes, or layout alignments).

### B. Ingesting Changes from the Other Agent
When pulling or reviewing changes from the partner agent:
1. **Read PR Body & Commit Details First**: Read the detailed description and comments using `gh pr view <PR#> --comments` or `git log -n 1 --stat`.
2. **Check for Contract/Layout Conflicts**:
   - Check if Unity layout changes affect WebXR layout or vice versa.
   - Check if API routes or authentication headers changed.
3. **Run Regression Tests**: Run the pytest suite or relevant tests before and after merging.
4. **Post Constructive Feedback**: If something breaks or conflicts, comment directly on the PR with specific error traces and suggested fixes.

---

## 2. Current Architecture & Alignment Status

### Layout Alignment: WebXR Cockpit vs. Unity PC-VR (`Barry.exe`)
- The WebXR cockpit (`backend/static/cockpit_vr.html`) has been aligned to the **36° curved cylindrical arc layout** matching Unity `Barry.exe`:
  - **Left Panel (-36°)**: Telemetry, Kinematics, Battery reserve, Pitch/Roll, and Virtual Analog Joystick.
  - **Center Panel (0°)**: Live DOGZILLA Front Camera Video Feed (relayed from Pi `:6500` or test feed), YOLO Detection Overlays, and Recenter HUD button.
  - **Right Panel (+36°)**: BARRY AI Brain (Claude / LLM status), Structured Voice Commands, and Inspection Event Logs.
  - **Floor Console / Bottom**: Emergency E-Stop button and system reset controls.
- **When Agent Beta transfers or refines Unity layouts**:
  - Keep coordinate frames and panel angular offsets consistent across Unity and WebXR.
  - Use `docs/joystick_and_flipper_mission_workflow.md` as reference for motion control and arbitration.

### Motion Control & Authentication Status (Full Access Mode)
- **Active Mission Gating**: Currently set to **Full Access Mode** (`REQUIRE_ACTIVE_MISSION=false` in `.env`).
  - Hand Joystick (`source="hand"`), Voice (`source="voice"`), and LLM (`source="llm"`) can move the robot freely without needing an active mission.
- **Card / Badge Provisioning**: Set to **Auto-Provision Mode** (`AUTH_ALLOW_UNREGISTERED_TAGS=true` in `.env`).
  - Any RFID/NFC tag UID scanned is automatically accepted and provisioned as an operator so tests never fail on missing credentials.
- **Anonymous Relay Allowed**: `QUEST_API_KEY_ALLOW_ANONYMOUS=true` is enabled for frictionless Unity and WebXR local streaming.

---

## 3. Standard Git Workflow
1. Never push directly to `main`.
2. Always create a feature branch (`git checkout -b <type>/<description>`).
3. Commit with descriptive messages.
4. Push branch and open a PR with `gh pr create`.
5. Verify mergeability (`gh pr view --json mergeable,mergeStateStatus`).
6. Merge using merge commit or squash, and delete branch (`gh pr merge --merge --delete-branch`).
7. Keep `main` deployable at all times.
