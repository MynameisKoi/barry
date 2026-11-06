"""Thread-safe Pi adapter for the Yahboom DOGZILLA SDK."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import threading
from typing import Any, Callable, Dict, Optional

from backend.robot.config import (
    APPROVED_ACTIONS, DISCRETE_STEP_FOR_INTENSITY, HEIGHT_PRESETS_Z,
    MOTION_SCALE_FOR_INTENSITY, PACE_FOR_INTENSITY, SDK_VX_MAX, SDK_VY_MAX,
    SDK_VYAW_MAX, SDK_VYAW_MIN_NONZERO, RobotConfig, check_attitude,
    check_combination,
)
from backend.robot.interface import (
    ActionInfo, CommandResult, CommandStatus, ControlState, Direction, Gait, HeightPreset, Intensity,
    MotionCommand, MovementStyle, Orientation, Posture, RobotInterface,
    RobotMode, Telemetry, apply_dead_zone,
)

logger = logging.getLogger("barry.robot.dogzilla")


class DogzillaController(RobotInterface):
    """Implement the v1 contract by composition with a serialized SDK object.

    The real SDK import is lazy. Import or serial-open failures leave a usable
    disconnected object. Injecting sdk avoids hardware access in tests.
    """

    def __init__(
        self,
        config: Optional[RobotConfig] = None,
        sdk: object | None = None,
        driver: object | None = None,
        max_speed: Optional[float] = None,
    ) -> None:
        sdk = sdk if sdk is not None else driver
        self.config = config or RobotConfig(mode=RobotMode.DOGZILLA)
        self._lock = threading.RLock()
        self._sdk = sdk
        self.dog = sdk
        self.driver = sdk
        self.max_speed = 1.0 if max_speed is None else max_speed
        self._connected = sdk is not None
        self._initialization_error: Optional[str] = None
        self._state = ControlState()
        self.imu_stabilization = True
        # Cached telemetry, written only by _refresh_hardware_telemetry.
        self.battery_level: Optional[float] = None
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        if sdk is None:
            try:
                from DOGZILLALib import DOGZILLA  # type: ignore[import-not-found]
                self._sdk = DOGZILLA(self.config.dogzilla_port)
                self.dog = self._sdk
                self.driver = self._sdk
                self._connected = True
            except Exception as exc:
                self._initialization_error = str(exc)
                logger.warning("DOGZILLA unavailable: %s", exc)

    @property
    def emergency_stopped(self) -> bool:
        """Return True if emergency stop latch is active."""
        with self._lock:
            return self._state.emergency_stopped

    @property
    def robot_mode(self) -> RobotMode:
        """Return the real-hardware deployment mode."""
        return RobotMode.DOGZILLA

    @property
    def connected(self) -> bool:
        """Return best-effort SDK health, updated after every attempted call."""
        with self._lock:
            return self._connected

    @staticmethod
    def _result(status: CommandStatus, command: str, message: str = "",
                intensity: Optional[Intensity] = None) -> CommandResult:
        return CommandResult(status, command, message, intensity)

    def _call(self, command: str, operation: Callable[[object], Any],
              *, stop_on_failure: bool = False) -> Optional[CommandResult]:
        """Make one logical SDK operation under the SDK-wide lock.

        An existing SDK object is always retried even after a transient error.
        Motion failures trigger a best-effort stop before the failed result is
        returned because an earlier call in a multi-axis operation may already
        have started the robot.
        """
        with self._lock:
            if self._sdk is None:
                detail = f": {self._initialization_error}" if self._initialization_error else ""
                return self._result(CommandStatus.FAILED, command, f"DOGZILLA is disconnected{detail}")
            try:
                operation(self._sdk)
                self._connected = True
            except Exception as exc:
                self._connected = False
                logger.exception("DOGZILLA %s failed", command)
                if stop_on_failure:
                    try:
                        self._sdk.stop()
                    except Exception:
                        logger.exception("Best-effort stop after %s failure also failed", command)
                return self._result(CommandStatus.FAILED, command, str(exc))
        return None

    def _motion_rejection(self, command: str) -> Optional[CommandResult]:
        if self._state.emergency_stopped:
            return self._result(CommandStatus.REJECTED, command, "Emergency stop is latched")
        reason = check_combination(self._state.gait, self._state.intensity, self._state.posture.height)
        return self._result(CommandStatus.REJECTED, command, reason) if reason else None

    def move_analog(self, command: MotionCommand) -> CommandResult:
        """Send scaled move_x/move_y/turn calls for normalized joystick axes.

        Scaling constants and intensity factors are UNCALIBRATED pending real
        hardware verification. The SDK promotes nonzero turns below 30 to 30,
        so Barry sends zero in that range to prevent a sudden yaw jump.
        """
        with self._lock:
            rejected = self._motion_rejection("move_analog")
            if rejected:
                return rejected
            if command.is_zero:
                return self.stop()
            scale = MOTION_SCALE_FOR_INTENSITY[self._state.intensity] * self.config.max_motion_scale
            x = round(apply_dead_zone(command.forward, self.config.dead_zone) * SDK_VX_MAX * scale)
            y = round(apply_dead_zone(command.strafe, self.config.dead_zone) * SDK_VY_MAX * scale)
            yaw = round(apply_dead_zone(command.turn, self.config.dead_zone) * SDK_VYAW_MAX * scale)
            # The SDK raises nonzero yaw below 30 to 30. Zero avoids a sudden
            # jump for small stick input.
            if 0 < abs(yaw) < SDK_VYAW_MIN_NONZERO:
                yaw = 0
            failed = self._call("move_analog", lambda dog: (
                dog.move_x(x), dog.move_y(y), dog.turn(yaw)
            ), stop_on_failure=True)
            if failed:
                return failed
            self._state.moving = bool(x or y or yaw)
            self._state.last_command = "move_analog"
            return self._result(CommandStatus.ACCEPTED, "move_analog")

    def move_discrete(self, direction: Direction,
                      intensity: Optional[Intensity] = None) -> CommandResult:
        """Call one directional SDK method with an UNCALIBRATED preset step."""
        try:
            direction = Direction(direction)
            selected = Intensity(intensity) if intensity is not None else self._state.intensity
        except ValueError as exc:
            return self._result(CommandStatus.REJECTED, "move_discrete", str(exc))
        with self._lock:
            rejected = self._motion_rejection("move_discrete")
            if rejected:
                return rejected
            steps = DISCRETE_STEP_FOR_INTENSITY[selected]
            method, step = {
                Direction.FORWARD: ("forward", steps["linear"]),
                Direction.BACKWARD: ("back", steps["linear"]),
                Direction.LEFT: ("left", steps["linear"]),
                Direction.RIGHT: ("right", steps["linear"]),
                Direction.TURN_LEFT: ("turnleft", steps["turn"]),
                Direction.TURN_RIGHT: ("turnright", steps["turn"]),
            }[direction]
            failed = self._call(
                "move_discrete", lambda dog: getattr(dog, method)(step),
                stop_on_failure=True,
            )
            if failed:
                return failed
            self._state.moving = True
            self._state.last_command = direction.value
            return self._result(CommandStatus.ACCEPTED, "move_discrete")

    def stop(self) -> CommandResult:
        """Always attempt SDK stop when an SDK object exists; preserve style."""
        with self._lock:
            intensity = self._state.intensity
            failed = self._call("stop", lambda dog: dog.stop())
            if failed:
                failed.intensity = intensity
                return failed
            self._state.moving = False
            if not self._state.emergency_stopped:
                self._state.last_command = "stop"
            return self._result(CommandStatus.STOP_DISPATCHED, "stop", intensity=intensity)

    def set_movement_style(self, style: MovementStyle) -> CommandResult:
        """Validate then call pace and gait_type without changing body height."""
        with self._lock:
            if self._state.emergency_stopped:
                return self._result(CommandStatus.REJECTED, "set_movement_style", "Emergency stop is latched")
            reason = check_combination(style.gait, style.intensity, self._state.posture.height)
            if reason:
                return self._result(CommandStatus.REJECTED, "set_movement_style", reason)
            failed = self._call("set_movement_style", lambda dog: (
                dog.pace(PACE_FOR_INTENSITY[style.intensity]),
                dog.gait_type(style.gait.value),
            ))
            if failed:
                return failed
            self._state.intensity, self._state.gait = style.intensity, style.gait
            self._state.last_command = "set_movement_style"
            return self._result(CommandStatus.ACCEPTED, "set_movement_style")

    def set_posture(
        self,
        posture: Optional[Posture] = None,
        *,
        height_z: Optional[float] = None,
        roll: Optional[float] = None,
        pitch: Optional[float] = None,
        yaw: Optional[float] = None,
        gait_type: Optional[str] = None,
        pace: Optional[str] = None,
        imu_enabled: Optional[bool] = None,
        preset: Optional[str] = None,
    ) -> Any:
        """Apply UNCALIBRATED height and safe roll/pitch/yaw SDK commands or legacy kwargs."""
        if posture is not None:
            with self._lock:
                if self._state.emergency_stopped:
                    return self._result(CommandStatus.REJECTED, "set_posture", "Emergency stop is latched")
                reason = check_attitude(posture.roll, posture.pitch, posture.yaw)
                reason = reason or check_combination(self._state.gait, self._state.intensity, posture.height)
                if reason:
                    return self._result(CommandStatus.REJECTED, "set_posture", reason)
                failed = self._call("set_posture", lambda dog: (
                    dog.translation("z", HEIGHT_PRESETS_Z[posture.height]),
                    dog.attitude("r", posture.roll),
                    dog.attitude("p", posture.pitch),
                    dog.attitude("y", posture.yaw),
                ))
                if failed:
                    return failed
                self._state.posture = posture
                self._state.last_command = "set_posture"
                return self._result(CommandStatus.ACCEPTED, "set_posture")

        return self._set_posture_legacy(
            height_z=height_z,
            roll=roll,
            pitch=pitch,
            yaw=yaw,
            gait_type=gait_type,
            pace=pace,
            imu_enabled=imu_enabled,
            preset=preset,
        )

    def list_actions(self) -> list[ActionInfo]:
        """Return only the configured, hardware-unverified action allowlist."""
        return [ActionInfo(number, name) for number, name in APPROVED_ACTIONS.items()]

    def perform_action(self, action_id: int) -> CommandResult:
        """Call action only for an allowlisted ID while safely stationary."""
        with self._lock:
            if action_id not in APPROVED_ACTIONS:
                return self._result(CommandStatus.REJECTED, "perform_action", "Action is not approved")
            if self._state.moving or self._state.emergency_stopped:
                return self._result(CommandStatus.REJECTED, "perform_action",
                                    "Actions require a stopped, non-e-stopped robot")
            failed = self._call("perform_action", lambda dog: dog.action(action_id))
            if failed:
                return failed
            self._state.last_command = f"action:{action_id}"
            return self._result(CommandStatus.ACCEPTED, "perform_action")

    def reset(self) -> CommandResult:
        """Call SDK reset, then restore the tracked pace and gait."""
        with self._lock:
            if self._state.emergency_stopped:
                return self._result(CommandStatus.REJECTED, "reset", "Emergency stop is latched")
            failed = self._call("reset", lambda dog: (
                dog.reset(), dog.pace(PACE_FOR_INTENSITY[self._state.intensity]),
                dog.gait_type(self._state.gait.value),
            ))
            if failed:
                return failed
            self._state.posture = Posture()
            self._state.moving = False
            self._state.last_command = "reset"
            return self._result(CommandStatus.ACCEPTED, "reset")

    def read_telemetry(self) -> Telemetry:
        """Read each measured value separately, recording failures as None."""
        values: Dict[str, Any] = {}
        errors: list[str] = []
        readers = {
            "battery": "read_battery", "roll": "read_roll",
            "pitch": "read_pitch", "yaw": "read_yaw", "firmware": "read_version",
        }
        for name, method in readers.items():
            with self._lock:
                if not self._connected or self._sdk is None:
                    values[name] = None
                    errors.append(f"{name}: disconnected")
                    continue
                try:
                    values[name] = getattr(self._sdk, method)()
                    self._connected = True
                except Exception as exc:
                    values[name] = None
                    errors.append(f"{name}: {exc}")
        return Telemetry(
            battery=values["battery"],
            orientation=Orientation(values["roll"], values["pitch"], values["yaw"]),
            firmware=values["firmware"], read_errors=errors,
            control_state=self.get_control_state(),
        )

    def get_control_state(self) -> ControlState:
        """Return a detached copy of application-tracked command state."""
        with self._lock:
            return ControlState(
                intensity=self._state.intensity, gait=self._state.gait,
                posture=self._state.posture, moving=self._state.moving,
                emergency_stopped=self._state.emergency_stopped,
                last_command=self._state.last_command,
            )

    def emergency_stop(self) -> Dict[str, Any]:
        """Attempt SDK stop, latch the software e-stop, then go limp.

        No motion command (e.g. ``reset``) is ever issued here: the robot must
        not move before the motors are unloaded.
        """
        with self._lock:
            result = self.stop()
            self._state.emergency_stopped = True
            self._state.moving = False
            self._state.last_command = "emergency_stop"
            unload = getattr(self.dog, "unload_allmotor", None) if self.dog is not None else None
            if callable(unload):
                try:
                    unload()
                except Exception:
                    self._connected = False
                    logger.exception("DOGZILLA unload_allmotor failed during emergency stop")
            return {"success": result.ok, "message": "Emergency stop latched",
                    "telemetry": self.get_status()}

    def reset_emergency_stop(self) -> Dict[str, Any]:
        """Reload motors and clear the latch; never commands motion.

        If reloading fails the latch stays set. The tracked posture is left
        untouched; call ``reset()`` afterwards to restore the default stance.
        """
        with self._lock:
            load = getattr(self.dog, "load_allmotor", None) if self.dog is not None else None
            if callable(load):
                try:
                    load()
                except Exception as exc:
                    self._connected = False
                    logger.exception("DOGZILLA load_allmotor failed; e-stop stays latched")
                    return {"success": False,
                            "message": f"Emergency stop NOT cleared: {exc}",
                            "telemetry": self.get_status()}
            self._state.emergency_stopped = False
            self._state.moving = False
            self._state.last_command = "emergency_stop_cleared"
            return {"success": True, "message": "Emergency stop cleared",
                    "telemetry": self.get_status()}

    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        """Serve the legacy API; left/right mean turn as in other backends."""
        if not isinstance(action, str):
            return {"success": False, "message": "Invalid action", "telemetry": self.get_status()}
        clean = action.strip().lower()
        if clean == "stop":
            result = self.stop()
        elif clean == "emergency_stop":
            return self.emergency_stop()
        elif self.emergency_stopped:
            return {
                "success": False,
                "error_code": "ESTOP_ACTIVE",
                "message": "Movement blocked: Emergency stop active.",
                "telemetry": self.get_status(),
            }
        else:
            directions = {
                "forward": Direction.FORWARD, "backward": Direction.BACKWARD,
                "left": Direction.TURN_LEFT, "right": Direction.TURN_RIGHT,
                "turn_left": Direction.TURN_LEFT, "turn_right": Direction.TURN_RIGHT,
                "strafe_left": Direction.LEFT, "strafe_right": Direction.RIGHT,
                "step_left": Direction.LEFT, "step_right": Direction.RIGHT,
            }
            if clean not in directions:
                return {"success": False, "message": f"Unknown movement action: {action}",
                        "telemetry": self.get_status()}
            try:
                value = float(speed)
                if not math.isfinite(value):
                    raise ValueError
            except (TypeError, ValueError):
                return {"success": False, "message": "Invalid speed", "telemetry": self.get_status()}
            value = max(0.0, min(1.0, value))
            intensity = Intensity.LOW if value < 1 / 3 else Intensity.MEDIUM if value < 2 / 3 else Intensity.HIGH
            result = self.move_discrete(directions[clean], intensity)
        return {"success": result.ok, "message": result.message or result.status.value,
                "telemetry": self.get_status()}

    def _legacy_result(self, success: bool, message: str,
                       error_code: Optional[str] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"success": success, "message": message}
        if error_code:
            out["error_code"] = error_code
        out["telemetry"] = self.get_status()
        return out

    @staticmethod
    def _nearest_height(z: float) -> HeightPreset:
        return min(HEIGHT_PRESETS_Z, key=lambda h: abs(HEIGHT_PRESETS_Z[h] - z))

    def _set_posture_legacy(
        self,
        *,
        height_z: Optional[float] = None,
        roll: Optional[float] = None,
        pitch: Optional[float] = None,
        yaw: Optional[float] = None,
        gait_type: Optional[str] = None,
        pace: Optional[str] = None,
        imu_enabled: Optional[bool] = None,
        preset: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Keyword posture path. Validates everything, then sends, then records.

        Nothing is sent to the SDK unless the whole request validates, and the
        resulting values are stored in ``self._state`` (never loose attributes).
        """
        with self._lock:
            if self._state.emergency_stopped:
                return self._legacy_result(False, "Posture change blocked: Emergency stop is active.",
                                           "ESTOP_ACTIVE")
            if not self._connected or self._sdk is None:
                return self._legacy_result(False, "Cannot set posture: Physical DOGZILLA hardware is offline.",
                                           "OFFLINE")

            st = self._state
            gait, intensity = st.gait, st.intensity
            height = st.posture.height
            r, p, y = st.posture.roll, st.posture.pitch, st.posture.yaw
            ops: list[Callable[[Any], Any]] = []
            pace_to_intensity = {v: k for k, v in PACE_FOR_INTENSITY.items()}

            try:
                if preset is not None:
                    name = str(preset).strip().lower()
                    if name == "obstacle_crossing":
                        gait, intensity, height = Gait.HIGH_WALK, Intensity.LOW, HeightPreset.MEDIUM
                        ops += [lambda d: d.gait_type("high_walk"), lambda d: d.pace("slow"),
                                lambda d, z=HEIGHT_PRESETS_Z[height]: d.translation("z", z)]
                    elif name == "crouch":
                        intensity, height = Intensity.LOW, HeightPreset.LOW
                        ops += [lambda d: d.pace("slow"),
                                lambda d, z=HEIGHT_PRESETS_Z[height]: d.translation("z", z)]
                    elif name in ("stand", "reset", "default"):
                        gait, intensity, height = Gait.TROT, Intensity.MEDIUM, HeightPreset.MEDIUM
                        r = p = y = 0.0
                        ops += [lambda d: d.reset(), lambda d: d.pace("normal"),
                                lambda d: d.gait_type("trot"),
                                lambda d, z=HEIGHT_PRESETS_Z[height]: d.translation("z", z)]
                    else:
                        return self._legacy_result(False, f"Unknown posture preset '{preset}'.")

                if height_z is not None:
                    z = float(height_z)
                    lo, hi = min(HEIGHT_PRESETS_Z.values()), max(HEIGHT_PRESETS_Z.values())
                    if not math.isfinite(z):
                        return self._legacy_result(False, "height_z must be a finite number.")
                    z = max(lo, min(hi, round(z)))
                    height = self._nearest_height(z)
                    ops.append(lambda d, z=z: d.translation("z", z))

                if roll is not None:
                    r = float(roll)
                    ops.append(lambda d, v=r: d.attitude("r", v))
                if pitch is not None:
                    p = float(pitch)
                    ops.append(lambda d, v=p: d.attitude("p", v))
                if yaw is not None:
                    y = float(yaw)
                    ops.append(lambda d, v=y: d.attitude("y", v))

                if gait_type is not None:
                    try:
                        gait = Gait(str(gait_type).strip().lower())
                    except ValueError:
                        return self._legacy_result(
                            False, f"Invalid gait_type '{gait_type}'. Must be trot, walk, or high_walk.")
                    ops.append(lambda d, v=gait.value: d.gait_type(v))

                if pace is not None:
                    pace_clean = str(pace).strip().lower()
                    if pace_clean not in pace_to_intensity:
                        return self._legacy_result(
                            False, f"Invalid pace '{pace}'. Must be slow, normal, or high.")
                    intensity = pace_to_intensity[pace_clean]
                    ops.append(lambda d, v=pace_clean: d.pace(v))
            except (TypeError, ValueError) as exc:
                return self._legacy_result(False, f"Invalid posture value: {exc}")

            reason = check_attitude(r, p, y) or check_combination(gait, intensity, height)
            if reason:
                return self._legacy_result(False, reason)

            if imu_enabled is not None:
                ops.append(lambda d: d.imu(1 if imu_enabled else 0))

            failed = self._call("set_posture", lambda dog: [op(dog) for op in ops])
            if failed:
                return self._legacy_result(False, f"DOGZILLA posture command failed: {failed.message}")

            st.gait, st.intensity = gait, intensity
            st.posture = Posture(height, r, p, y)
            if imu_enabled is not None:
                self.imu_stabilization = bool(imu_enabled)
            if preset is not None and str(preset).strip().lower() in ("stand", "reset", "default"):
                st.moving = False
            st.last_command = f"posture:{preset or 'custom'}"
            return self._legacy_result(True, "DOGZILLA posture updated.")

    def _refresh_hardware_telemetry(self) -> None:
        """Poll battery and IMU roll/pitch/yaw into the cache under the SDK lock."""
        with self._lock:
            if not self._connected or self._sdk is None:
                return
            try:
                bat = self._sdk.read_battery()
                if isinstance(bat, (int, float)) and 0 <= bat <= 100:
                    self.battery_level = float(bat)
                self.roll = float(self._sdk.read_roll())
                self.pitch = float(self._sdk.read_pitch())
                self.yaw = float(self._sdk.read_yaw())
            except Exception as exc:
                logger.debug("Telemetry poll skipped: %s", exc)

    def get_status(self) -> Dict[str, Any]:
        """Return the legacy status shape from tracked state plus cached telemetry."""
        self._refresh_hardware_telemetry()
        state = self.get_control_state()
        with self._lock:
            connected = self._connected
            battery, roll, pitch, yaw = self.battery_level, self.roll, self.pitch, self.yaw
            imu_stabilization = self.imu_stabilization
        return {
            "connected": connected,
            "mode": "dogzilla",
            "battery_level": battery,
            "position": {"x": 0.0, "y": 0.0, "heading": yaw},
            "imu": {
                "roll": roll,
                "pitch": pitch,
                "yaw": yaw,
                "imu_stabilization": imu_stabilization,
            },
            "speed": 1.0 if state.moving else 0.0,
            "last_command": state.last_command,
            "emergency_stopped": state.emergency_stopped,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
