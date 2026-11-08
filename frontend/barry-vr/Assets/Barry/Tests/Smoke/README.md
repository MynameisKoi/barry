# Barry robot API smoke test

This smoke test uses the real `BarryApiClient` against the standalone robot API. It calls, in order:

1. `GET /api/v1/status`
2. `GET /api/v1/telemetry`
3. `GET /api/v1/actions`
4. `POST /api/v1/session`
5. `POST /api/v1/motion` with `{ "forward": 0.2, "strafe": 0, "turn": 0 }`
6. `POST /api/v1/stop`
7. `GET /api/v1/status`

The motion is sent only to the simulator in the commands below. The test has a 20-second overall timeout and exits Unity with status 0 only when every HTTP response parses and succeeds.

## 1. Start the robot API in simulator mode

From the `barry` repository root:

```bash
cd '/Users/Local Documents/Local Documents/barry'
ROBOT_MODE=simulator \
ROBOT_API_KEY=barry-smoke-test-key \
.venv/bin/uvicorn backend.robot.server:app --host 127.0.0.1 --port 8100
```

This is the Pi-side robot API entry point, running the in-process `RobotSimulator`; it does not start the laptop aggregate API or access DOGZILLA hardware.

## 2. Run the Unity smoke entry point

In a second terminal, from the same repository root:

```bash
cd '/Users/Local Documents/Local Documents/barry'
UNITY='/Applications/Unity/Hub/Editor/6000.6.8f1/Unity.app/Contents/MacOS/Unity'
BARRY_BASE_URL=http://127.0.0.1:8100 \
BARRY_ROBOT_KEY=barry-smoke-test-key \
"$UNITY" -batchmode \
  -projectPath '/Users/Local Documents/Local Documents/barry/frontend/barry-vr' \
  -executeMethod Barry.Tests.Smoke.ApiSmoke.Run \
  -logFile -
```

Do **not** add `-quit`: `ApiSmoke.Run` must return control to the Editor update loop to pump the real `UnityWebRequest` coroutines, and it calls `EditorApplication.Exit(0 or 1)` itself. If Unity is installed elsewhere, change only the `UNITY` path.

Each request prints one line containing its method, path, HTTP status, parsed `OK`/`FAIL`, and elapsed milliseconds. The final line is `API smoke PASS` or `API smoke FAIL`.
