"""Laptop-side ``RobotInterface`` that forwards commands to the Pi Robot API.

``RemoteRobotClient`` is selected by ``ROBOT_MODE=remote``. It speaks the wire
contract in ``backend.robot.protocol`` over HTTP (``httpx``) and turns the JSON
replies back into the value objects of ``backend.robot.interface``.

Behaviour guarantees
--------------------
* **Auth**: every request carries ``X-Robot-Key`` (``config.api_key``).
* **Control session**: acquired lazily by the first mutating command and kept in
  ``X-Control-Session``. A 409 reply is treated as "session expired or
  replaced": the session is re-acquired and the command is retried **once**
  with a fresh sequence number. A second 409 is returned as ``REJECTED``.
* **Sequence numbers**: ``X-Command-Seq`` is strictly increasing across all
  mutating commands of this client (also across re-acquired sessions, which is
  valid for a per-session "strictly increasing" rule). Sequencing and sending
  are serialized so two threads cannot deliver seq N+1 before seq N.
* **Link loss never raises**: timeouts and connection errors become
  ``CommandResult(FAILED)``, ``connected`` turns ``False`` and legacy
  ``get_status()`` reports ``connected: false``. The next successful reply
  flips ``connected`` back.
* **Stop priority**: ``stop()`` and ``emergency_stop()`` need only the API key
  per the contract. They never take the command lock and never wait for
  session acquisition, so a slow or hung motion request cannot delay them.
* A result with status ``accepted`` means "the Pi accepted it", never that the
  robot physically moved (see ``interface.py``).

Not verified against a real Pi yet: the exact 409 body distinguishing "stale
sequence" from "wrong session" is not specified in ``protocol.py``, so every
409 on a command triggers one re-acquire-and-retry. UNCALIBRATED assumption.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.robot import protocol
from backend.robot.config import RobotConfig
from backend.robot.interface import (
    ActionInfo,
    CommandResult,
    CommandStatus,
    ControlState,
    Direction,
    Gait,
    HeightPreset,
    Intensity,
    MotionCommand,
    MovementStyle,
    Orientation,
    Posture,
    RobotInterface,
    RobotMode,
    Telemetry,
)

logger = logging.getLogger(__name__)

#: HTTP codes that mean "the Pi understood and refused" (vs. a failure).
_REJECTION_CODES = {
    protocol.HTTP_UNAUTHORIZED,
    protocol.HTTP_CONFLICT,
    protocol.HTTP_UNPROCESSABLE,
    protocol.HTTP_LOCKED,
}

_LEGACY_MOVES = {"forward", "backward", "left", "right"}


class RemoteRobotClient(RobotInterface):
    """``RobotInterface`` implemented over the Pi's HTTP Robot API."""

    def __init__(self, config: RobotConfig, transport: Optional[httpx.BaseTransport] = None) -> None:
        """
        :param config: Uses ``remote_url``, ``api_key`` and ``remote_timeout_s``.
        :param transport: Optional ``httpx`` transport; tests pass
            ``httpx.MockTransport`` so no network is needed.
        """
        self.config = config
        headers = {protocol.AUTH_HEADER: config.api_key} if config.api_key else {}
        self._http = httpx.Client(
            base_url=config.remote_url,
            timeout=config.remote_timeout_s,
            headers=headers,
            transport=transport,
        )

        # Serializes session acquisition + seq assignment + send for mutating
        # commands. stop()/emergency_stop() deliberately do NOT use it.
        self._cmd_lock = threading.Lock()
        # Guards the small bits of shared state below (never held during I/O).
        self._state_lock = threading.RLock()

        self._session_id: Optional[str] = None
        self._seq = 0
        self._link_ok = True
        self._state = ControlState()  # best-effort mirror of the Pi's tracked state
        self._last_telemetry: Optional[Telemetry] = None
        self._closed = False

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def robot_mode(self) -> RobotMode:
        return RobotMode.REMOTE

    @property
    def connected(self) -> bool:
        """``False`` after a link error / 503 until a request succeeds again."""
        return self._link_ok

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def move_analog(self, command: MotionCommand) -> CommandResult:
        body = {"forward": command.forward, "strafe": command.strafe, "turn": command.turn}
        result = self._command("motion", protocol.PATH_MOTION, body)
        if result.ok:
            self._track(moving=not command.is_zero, last_command="stop" if command.is_zero else "motion")
        return result

    def move_discrete(self, direction: Direction, intensity: Optional[Intensity] = None) -> CommandResult:
        body: Dict[str, Any] = {"direction": Direction(direction).value}
        if intensity is not None:
            body["intensity"] = Intensity(intensity).value
        result = self._command("motion_discrete", protocol.PATH_MOTION_DISCRETE, body)
        if result.ok:
            self._track(moving=True, last_command=body["direction"])
        return result

    def stop(self) -> CommandResult:
        """Sends stop with the API key only: no session, no sequence number."""
        result = self._send_priority("stop", protocol.PATH_STOP)
        if result.ok:
            self._track(moving=False, last_command="stop")
        if result.intensity is None:
            result.intensity = self._state.intensity
        return result

    # ------------------------------------------------------------------
    # Style, posture, actions, reset
    # ------------------------------------------------------------------

    def set_movement_style(self, style: MovementStyle) -> CommandResult:
        body = {"intensity": style.intensity.value, "gait": style.gait.value}
        result = self._command("movement_style", protocol.PATH_MOVEMENT_STYLE, body)
        if result.ok:
            self._track(intensity=style.intensity, gait=style.gait)
        return result

    def set_posture(self, posture: Posture) -> CommandResult:
        body = {
            "height": posture.height.value,
            "roll": posture.roll,
            "pitch": posture.pitch,
            "yaw": posture.yaw,
        }
        result = self._command("posture", protocol.PATH_POSTURE, body)
        if result.ok:
            self._track(posture=posture)
        return result

    def list_actions(self) -> List[ActionInfo]:
        """Approved actions from the Pi; an empty list if the Pi is unreachable."""
        resp, error = self._call("GET", protocol.PATH_ACTIONS)
        if resp is None or resp.status_code != 200:
            logger.warning("list_actions failed: %s", error or (resp.status_code if resp is not None else "?"))
            return []
        try:
            return [ActionInfo(id=int(a["id"]), name=str(a["name"])) for a in resp.json().get("actions", [])]
        except (ValueError, KeyError, TypeError, AttributeError):
            logger.warning("list_actions: malformed reply")
            return []

    def perform_action(self, action_id: int) -> CommandResult:
        if isinstance(action_id, bool) or not isinstance(action_id, int):
            return CommandResult(CommandStatus.REJECTED, "action", f"Invalid action id {action_id!r}.")
        result = self._command("action", protocol.PATH_ACTION.format(action_id=action_id), None)
        if result.ok:
            self._track(last_command=f"action:{action_id}")
        return result

    def reset(self) -> CommandResult:
        result = self._command("reset", protocol.PATH_RESET, None)
        if result.ok:
            self._track(posture=Posture(), moving=False, last_command="reset")
        return result

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def read_telemetry(self) -> Telemetry:
        """Fetches telemetry. On link loss returns an empty measurement with the
        reason in ``read_errors`` and the last known tracked state; measured
        fields are ``None`` rather than stale or invented values."""
        resp, error = self._call("GET", protocol.PATH_TELEMETRY)
        if resp is not None and resp.status_code == 200:
            try:
                telemetry = self._parse_telemetry(resp.json())
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                error = f"malformed telemetry reply: {exc}"
            else:
                with self._state_lock:
                    self._state = telemetry.control_state
                    self._last_telemetry = telemetry
                return telemetry
        elif resp is not None:
            error = error or f"HTTP {resp.status_code}"
        return Telemetry(read_errors=[error or "telemetry unavailable"], control_state=self.get_control_state())

    def get_control_state(self) -> ControlState:
        """Last known tracked state (mirror of the Pi); never makes a request."""
        with self._state_lock:
            s = self._state
            return ControlState(
                intensity=s.intensity,
                gait=s.gait,
                posture=Posture(s.posture.height, s.posture.roll, s.posture.pitch, s.posture.yaw),
                moving=s.moving,
                emergency_stopped=s.emergency_stopped,
                last_command=s.last_command,
            )

    # ------------------------------------------------------------------
    # Emergency stop latch
    # ------------------------------------------------------------------

    def emergency_stop(self) -> Dict[str, Any]:
        """Latches the e-stop on the Pi. Needs only the API key; never waits on a session."""
        result = self._send_priority("emergency_stop", protocol.PATH_EMERGENCY_STOP)
        if result.ok:
            self._track(moving=False, emergency_stopped=True, last_command="emergency_stop")
        return self._legacy_result(result)

    def reset_emergency_stop(self) -> Dict[str, Any]:
        """Clears the latch. Treated as a normal (session + seq) command: the
        contract exempts only stop and emergency-stop from the session."""
        result = self._command("emergency_reset", protocol.PATH_EMERGENCY_RESET, None)
        if result.ok:
            self._track(emergency_stopped=False, moving=False, last_command="stop")
        return self._legacy_result(result)

    # ------------------------------------------------------------------
    # Legacy API (backend/main.py)
    # ------------------------------------------------------------------

    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        """Maps the legacy one-shot actions onto v1 analog motion.

        ``forward/backward`` -> ``forward=+/-speed``; ``left/right`` -> turn in
        place (``turn=+/-speed``), matching the simulator's legacy behaviour.
        """
        if not isinstance(action, str):
            return self._legacy_failure(f"Invalid action: {action!r}. Expected a string.")
        action = action.lower().strip()
        if action == "stop":
            return self._legacy_result(self.stop())
        if action == "emergency_stop":
            return self.emergency_stop()
        if action not in _LEGACY_MOVES:
            return self._legacy_failure(f"Unknown movement action: '{action}'.")
        try:
            value = float(speed)
        except (TypeError, ValueError):
            return self._legacy_failure(f"Invalid speed: {speed!r}.")
        if value != value:  # NaN
            return self._legacy_failure("Invalid speed: NaN.")
        value = max(0.0, min(1.0, value))

        axes = {
            "forward": (value, 0.0),
            "backward": (-value, 0.0),
            "left": (0.0, value),
            "right": (0.0, -value),
        }[action]
        return self._legacy_result(self.move_analog(MotionCommand(forward=axes[0], turn=axes[1])))

    def get_status(self) -> Dict[str, Any]:
        """Legacy status dict built from ``GET /telemetry``.

        The Pi does not report a world position, so ``position`` and ``speed``
        are zero placeholders (same as ``DogzillaController``); battery is
        ``0.0`` when unavailable. ``connected`` reflects the real link state.
        """
        telemetry = self.read_telemetry()
        return self._status_dict(telemetry)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Releases the control session (which stops the robot) and closes HTTP. Idempotent."""
        if self._closed:
            return
        self._closed = True
        with self._state_lock:
            session_id, self._session_id = self._session_id, None
        if session_id:
            self._call("DELETE", protocol.PATH_SESSION, headers={protocol.SESSION_HEADER: session_id})
        self._http.close()

    def __enter__(self) -> "RemoteRobotClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internals: HTTP
    # ------------------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[httpx.Response], Optional[str]]:
        """One request. Returns ``(response, None)`` or ``(None, error_text)``.

        Never raises for transport problems; updates the link-health flag.
        """
        if self._closed and method != "DELETE":
            return None, "client closed"
        try:
            resp = self._http.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            self._link_ok = False
            return None, f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        # A 503 means the Pi is up but the robot hardware is not.
        self._link_ok = resp.status_code != protocol.HTTP_UNAVAILABLE
        return resp, None

    def _send_priority(self, name: str, path: str) -> CommandResult:
        """stop / emergency-stop: key only, no lock, no session, no seq."""
        resp, error = self._call("POST", path)
        if resp is None:
            return CommandResult(CommandStatus.FAILED, name, f"Link error: {error}")
        return self._to_result(name, resp)

    def _command(self, name: str, path: str, body: Any) -> CommandResult:
        """Mutating command: lazy session, fresh seq, one re-acquire on 409."""
        with self._cmd_lock:
            for attempt in (0, 1):
                failure = self._ensure_session(name)
                if failure is not None:
                    return failure
                with self._state_lock:
                    self._seq += 1
                    headers = {
                        protocol.SESSION_HEADER: self._session_id or "",
                        protocol.SEQ_HEADER: str(self._seq),
                        protocol.SENT_AT_HEADER: str(int(time.time() * 1000)),
                    }
                resp, error = self._call("POST", path, json=body, headers=headers)
                if resp is None:
                    return CommandResult(CommandStatus.FAILED, name, f"Link error: {error}")
                if resp.status_code == protocol.HTTP_CONFLICT and attempt == 0:
                    # Session expired / replaced: forget it and retry once.
                    with self._state_lock:
                        self._session_id = None
                    continue
                return self._to_result(name, resp)
        return CommandResult(CommandStatus.FAILED, name, "unreachable")  # pragma: no cover

    def _ensure_session(self, name: str) -> Optional[CommandResult]:
        """Acquires the control session if we have none. ``None`` means ready;
        otherwise the ``CommandResult`` to return. Caller holds ``_cmd_lock``."""
        if self._session_id:
            return None
        resp, error = self._call("POST", protocol.PATH_SESSION)
        if resp is None:
            return CommandResult(CommandStatus.FAILED, name, f"Link error: {error}")
        if resp.status_code != 200 and resp.status_code != 201:
            result = self._to_result(name, resp)
            if resp.status_code == protocol.HTTP_CONFLICT:
                result.message = result.message or "Control session held by another operator."
            return result
        try:
            session_id = str(resp.json()["session_id"])
        except (ValueError, KeyError, TypeError):
            return CommandResult(CommandStatus.FAILED, name, "Malformed session reply.")
        with self._state_lock:
            self._session_id = session_id
        return None

    # ------------------------------------------------------------------
    # Internals: mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _body(resp: httpx.Response) -> Dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _to_result(self, name: str, resp: httpx.Response) -> CommandResult:
        """Maps an HTTP reply to a ``CommandResult``."""
        data = self._body(resp)
        if 200 <= resp.status_code < 300:
            try:
                intensity = data.get("intensity")
                return CommandResult(
                    status=CommandStatus(data["status"]),
                    command=str(data.get("command", name)),
                    message=str(data.get("message", "")),
                    intensity=Intensity(intensity) if intensity else None,
                )
            except (KeyError, ValueError):
                return CommandResult(CommandStatus.FAILED, name, "Malformed reply from robot API.")

        detail = data.get("message") or data.get("detail") or resp.reason_phrase or f"HTTP {resp.status_code}"
        status = CommandStatus.REJECTED if resp.status_code in _REJECTION_CODES else CommandStatus.FAILED
        return CommandResult(status, name, f"HTTP {resp.status_code}: {detail}")

    @staticmethod
    def _parse_control_state(data: Dict[str, Any]) -> ControlState:
        posture = data.get("posture") or {}
        return ControlState(
            intensity=Intensity(data.get("intensity", "medium")),
            gait=Gait(data.get("gait", "trot")),
            posture=Posture(
                HeightPreset(posture.get("height", "medium")),
                posture.get("roll", 0.0),
                posture.get("pitch", 0.0),
                posture.get("yaw", 0.0),
            ),
            moving=bool(data.get("moving", False)),
            emergency_stopped=bool(data.get("emergency_stopped", False)),
            last_command=str(data.get("last_command", "stop")),
        )

    def _parse_telemetry(self, data: Dict[str, Any]) -> Telemetry:
        orientation = data.get("orientation") or {}
        return Telemetry(
            battery=data.get("battery"),
            orientation=Orientation(
                roll=orientation.get("roll"), pitch=orientation.get("pitch"), yaw=orientation.get("yaw")
            ),
            firmware=data.get("firmware"),
            read_errors=[str(e) for e in data.get("read_errors", [])],
            control_state=self._parse_control_state(data.get("control_state") or {}),
        )

    # ------------------------------------------------------------------
    # Internals: tracked state and legacy shapes
    # ------------------------------------------------------------------

    def _track(self, **changes: Any) -> None:
        """Updates the local mirror of the Pi's tracked state after an accepted command."""
        with self._state_lock:
            for key, value in changes.items():
                setattr(self._state, key, value)

    def _status_dict(self, telemetry: Telemetry) -> Dict[str, Any]:
        from datetime import datetime, timezone

        state = telemetry.control_state
        return {
            "connected": self.connected,
            "mode": "remote",
            "battery_level": float(telemetry.battery) if telemetry.battery is not None else 0.0,
            "position": {"x": 0.0, "y": 0.0, "heading": 0.0},
            "speed": 0.0,
            "last_command": state.last_command,
            "emergency_stopped": state.emergency_stopped,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _legacy_result(self, result: CommandResult) -> Dict[str, Any]:
        """Legacy ``{success, message, telemetry}`` dict from a v1 result.

        Uses the cached telemetry so a legacy move does not trigger a second
        network request per command.
        """
        with self._state_lock:
            cached = self._last_telemetry or Telemetry()
            telemetry = Telemetry(battery=cached.battery, control_state=self.get_control_state())
        return {"success": result.ok, "message": result.message, "telemetry": self._status_dict(telemetry)}

    def _legacy_failure(self, message: str) -> Dict[str, Any]:
        return self._legacy_result(CommandResult(CommandStatus.REJECTED, "move", message))
