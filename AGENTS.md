# BARRY: agent team rules and collaboration protocol

One source of truth for every agent (Tomville team agents and partner agents) working on this repository.

## 0. Team (Tomville office)

- **Lead:** Claude Opus (`claude`). Splits the plan into GitHub issues, reviews, tests and **merges** PRs, but only with Tom's approval.
- **Workers:** Claude Sonnet (`claude2`), Codex (`codex1`, `codex2`). Work only on tasks the lead assigned; never merge, including their own PRs.
- **Partner agents / human teammates** (e.g. Agent Alpha / Agent Beta below) follow the same PR protocol and also do not self-merge.
- Plan: `docs/control-plan.md`. Read it first and follow the documents it links to.

### Tomville commands
- Message another agent: `tmv send AGENT "text"` (not `herdr agent prompt`; tmv logs the sender).
- Work items are GitHub issues: `tmv task new "title"`, `tmv task take N`, `tmv task move N review`, `tmv task list`.
- Lead only: with `[board] watch = true`, keep `tmv watch` running and restart it after handling each change.

### Everyone
- Work only inside the project root; don't read files outside it unless given the exact path.
- Never read or print secrets (`.env`, API keys, `ROBOT_API_KEY`, `CAMERA_API_KEY`, key files). Use `.env.example` for setting names.
- Never commit to `main`; one branch and one PR per task. No force-push, no history rewrites, no deleting branches you didn't create.
- No system-wide installs; project dependencies go in the project's own requirement files.
- Don't connect to the real robot, Raspberry Pi, Quest headset or Flipper Zero. Use `ROBOT_MODE=simulation` and `CAMERA_DEVICE=synthetic`. Real-hardware tests are run by Tom.
- Never weaken emergency-stop, stop, watchdog or speed-cap behaviour. If a task seems to need that, stop and ask the lead.
- Run the tests that cover your change before asking for review, and list them with their results in the PR.

---

## 1. Pull requests and reviews

### A. Every PR body MUST include
1. **Context & motivation**: exactly what problem is solved and why.
2. **Component changes**: backend, API, frontend/Unity or WebXR, file by file.
3. **Contract / wire changes**: endpoints, headers, request/response models, env vars, defaults.
4. **Verification proofs**: exact commands with the output tail, screenshots or logs.
5. **Impact on other agents**: what they must do (pull, restart, env variable changes, layout alignment).

### B. Ingesting changes from another agent
1. **Read the PR body and commits first**: `gh pr view <N> --comments`, `gh pr diff <N>`, `git log -n 1 --stat`.
2. **Check for contract/layout conflicts**: Unity ↔ WebXR layout, API routes, auth headers, safety behaviour.
3. **Run regression tests** before and after (`pytest -q tests`).
4. **Comment on the PR** with the decision and reasoning; on problems, include the error trace and a suggested fix.

### C. Workflow
1. Branch per task (`<type>/<description>`), descriptive commits, `gh pr create` with the body above.
2. Check mergeability: `gh pr view --json mergeable,mergeStateStatus`.
3. Worker: `tmv task move N review`, then `tmv send claude "PR #N ready"`.
4. **Only the lead merges**, after review, tests and Tom's approval. Keep `main` deployable.

---

## 2. Current architecture and alignment status

### WebXR cockpit = 1:1 port of the Unity dashboard
`backend/static/cockpit_vr.html` is a port of `frontend/barry-vr/Assets/Barry/App/Editor/DashboardSceneBuilder.cs`
(same positions, sizes, Galactic theme tokens, copy and controller mapping; three.js r187 self-hosted in
`backend/static/vendor/`). **Any Unity layout change must be mirrored in the WebXR page in the same PR, or in a linked follow-up.**

| Panel | Unity placement (eye 1.6 m, +Z forward) | Notes |
|---|---|---|
| Header strip | (0, 1.6 + 0.588, 2.0) | BARRY, mission chip, e-stop / robot / control chips |
| Camera screen | (0, 1.6, 2.0), 1.80 m wide | MJPEG from `/api/v1/camera/stream`, else the dog relay `/api/camera/stream` |
| Status panel | arc −36°, 1.3 m | robot, control, camera fps, RTT, battery, COMMAND SENT bars |
| Actions panel | arc +36°, 1.3 m | collapsed `ACTIONS · B` pill; list from `/api/v1/actions` |
| Brain panel | arc +46°, 1.25 m, y + 0.1 | collapsed `BRAIN · Y` tab; `/api/v1/llm/*` |
| Control strip | 35° below eye at 0.9 m, tilted 30° up | intensity, STOP, Exit VR, End mission |
| Mission card | (0, 1.6 − 0.22, 1.35) | shown while no mission is active |
| E-stop banner | (0, 1.6 + 0.12, 1.6) | shown while latched; reset needs two presses |

Conversion to three.js: `(x, y, z)` → `(x, y, −z)`, yaw `a` → `rotation.y = −a`, pitch `p` → `rotation.x = −p`.
Controller mapping (HANDOFF §6): left stick move/strafe, right stick X turn, A = STOP, both grips + A held 0.3 s = e-stop,
B = actions, X = intensity, Y = brain. Release of the sticks sends STOP; after any stop the sticks must be centred before driving again.
WebXR only: hold Y (>= 0.35 s) = push-to-talk to `POST /api/v1/voice/command` (tap Y still toggles the Brain); point at a panel and hold grip to move it (driving is paused while holding; saved per headset in localStorage);
both thumbsticks clicked in for 1 s resets the layout. The control strip sits 35° below the eye line (tilted 30°) in both apps.
Camera: with `ROBOT_MODE=remote` the laptop camera service (YOLO, `/api/v1/camera/*`) reads the dog's MJPEG feed
(`DOG_VIDEO_URL`, else port 6500 on the `ROBOT_REMOTE_URL` host), never the laptop webcam; `CAMERA_SOURCE=dog` forces it.

### Motion control and authentication (testing defaults; Tom decided on 2026-11-12 to keep them as they are)
- `REQUIRE_ACTIVE_MISSION=false`: hand, voice and LLM can move the robot without an active mission.
- `AUTH_ALLOW_UNREGISTERED_TAGS=true`: any scanned tag UID is auto-provisioned as an operator.
- `QUEST_API_KEY_ALLOW_ANONYMOUS=true`, `CAMERA_API_KEY_ALLOW_ANONYMOUS=true`: anonymous relay for local tests.
