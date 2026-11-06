"""Wire contract between the laptop (``remote.py``) and the Pi (``server.py``).

Both sides import these constants so paths, headers and error codes can never
drift apart. Request/response bodies are the ``to_dict()`` shapes defined in
``backend.robot.interface`` (``CommandResult``, ``Telemetry``, ...).

Authentication
    Every route except ``GET /healthz`` requires ``X-Robot-Key`` to equal
    ``ROBOT_API_KEY``. Missing/wrong key -> 401.

Control session (single active operator)
    ``POST   /api/v1/session``  acquires the lease and returns
    ``{"session_id": str, "ttl_s": float}``. 409 if another live session holds it.
    ``DELETE /api/v1/session``  releases the lease and stops the robot.
    Every *mutating* route except stop / emergency-stop requires
    ``X-Control-Session`` to match the active session (else 409). Each
    accepted command refreshes the lease; an idle lease expires after
    ``ROBOT_SESSION_TTL_S`` and the robot is stopped.

Stale-command rejection
    Mutating requests carry ``X-Command-Seq``: a per-session strictly
    increasing integer. ``seq <= last accepted seq`` -> 409 (stale).
    Optional ``X-Command-Sent-At`` (sender epoch ms) is checked against
    ``ROBOT_MAX_COMMAND_AGE_MS`` when that is > 0.

Stop priority
    ``/stop`` and ``/emergency-stop`` need only a valid API key: no session,
    no sequence number. They invalidate any queued motion and are never
    blocked behind another command.

Watchdog
    The Pi stops the robot when no motion command arrives within
    ``ROBOT_WATCHDOG_MS`` while it is moving. Clients resend motion while a
    joystick is held (recommended every 100-200 ms).
"""

from __future__ import annotations

API_PREFIX = "/api/v1"

# --- headers ------------------------------------------------------------
AUTH_HEADER = "X-Robot-Key"
SESSION_HEADER = "X-Control-Session"
SEQ_HEADER = "X-Command-Seq"
SENT_AT_HEADER = "X-Command-Sent-At"

# --- paths --------------------------------------------------------------
PATH_HEALTH = "/healthz"
PATH_SESSION = f"{API_PREFIX}/session"
PATH_MOTION = f"{API_PREFIX}/motion"
PATH_MOTION_DISCRETE = f"{API_PREFIX}/motion/discrete"
PATH_STOP = f"{API_PREFIX}/stop"
PATH_EMERGENCY_STOP = f"{API_PREFIX}/emergency-stop"
PATH_EMERGENCY_RESET = f"{API_PREFIX}/emergency-stop/reset"
PATH_MOVEMENT_STYLE = f"{API_PREFIX}/movement-style"
PATH_POSTURE = f"{API_PREFIX}/posture"
PATH_ACTIONS = f"{API_PREFIX}/actions"
PATH_ACTION = f"{API_PREFIX}/actions/{{action_id}}"
PATH_RESET = f"{API_PREFIX}/reset"
PATH_TELEMETRY = f"{API_PREFIX}/telemetry"
PATH_STATUS = f"{API_PREFIX}/status"

# --- HTTP status codes used by the Pi API ----------------------------------
HTTP_UNAUTHORIZED = 401      # bad / missing X-Robot-Key
HTTP_CONFLICT = 409          # session held by someone else, wrong session, stale seq
HTTP_LOCKED = 423            # emergency stop latched
HTTP_UNPROCESSABLE = 422     # validation failure or unsafe combination
HTTP_UNAVAILABLE = 503       # hardware disconnected
