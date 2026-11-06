"""DOGZILLA S1/S2 Physical Hardware Controller for BARRY (`backend/robot/dogzilla.py`).

Integrates Yahboom `DOGZILLALib.DOGZILLA` (v2.0.7) with:
- Thread-safe serial locking (`threading.RLock`) so concurrent commands and IMU/battery reads
  never interleave bytes or corrupt `DOGZILLALib.ORDER` / `rx_data`.
- Accurate kinematic scaling from normalized speed `[0.0, 1.0]` to hardware step units:
  - X translation velocity (`move_x`): `2..20` mm (`PARAM["VX_LIMIT"] = 25`, max step `20`)
  - Y lateral strafe (`move_y`): `2..18` mm (`PARAM["VY_LIMIT"] = 18`)
  - Yaw rotation (`turn`): `30..70` deg/s (respecting `DOGZILLALib.turn` deadband `30..70`)
- Full emergency stop latch (`stop()` + `reset()` + `unload_allmotor()`) and reload (`load_allmotor()`).
- Real hardware telemetry (`read_battery`, `read_roll`, `read_pitch`, `read_yaw`, `read_motor`, `read_version`).
- Inspection posture, camera pitch/yaw look-around, gait (`trot`/`walk`/`high_walk`), pace, and IMU control.
"""

from datetime import datetime, timezone
import logging
import threading
from typing import Any, Dict, List, Optional

from backend.robot.config import get_robot_config
from backend.robot.DOGZILLALib import DOGZILLA, PARAM
from backend.robot.interface import RobotInterface

logger = logging.getLogger("barry.robot.dogzilla")


class DogzillaController(RobotInterface):
    """Thread-safe controller for the physical DOGZILLA S1/S2 quadruped robot."""

    # Hardware limits derived directly from DOGZILLALib v2.0.7 & joystick_dogzilla.py
    MAX_STEP_X: int = 20          # move_x clamps to [-20, 20], VX_LIMIT = 25
    MAX_STEP_Y: int = 18          # move_y clamps to [-18, 18], VY_LIMIT = 18
    MIN_TURN_STEP: int = 30       # turn() deadband snaps (0, 30) -> 30
    MAX_TURN_STEP: int = 70       # turn() clamps to [-70, 70], VYAW_LIMIT = 100
    MIN_HEIGHT_Z: int = 75        # PARAM["TRANSLATION_LIMIT"][2] = [75, 115]
    MAX_HEIGHT_Z: int = 115
    DEFAULT_HEIGHT_Z: int = 105   # Default standing height from joystick_dogzilla.py

    def __init__(
        self,
        device_endpoint: Optional[str] = None,
        driver: Optional[Any] = None,
        max_speed: Optional[float] = None,
    ) -> None:
        cfg = get_robot_config()
        self.device_endpoint = device_endpoint or cfg.DOGZILLA_PORT
        self.max_speed = max_speed if max_speed is not None else cfg.ROBOT_MAX_SPEED
        self._lock = threading.RLock()

        self.dog: Optional[Any] = driver
        self.connected: bool = driver is not None
        self.mode: str = "physical"
        self.firmware_version: Optional[str] = None

        self.battery_level: float = 0.0
        self.speed: float = 0.0
        self.last_command: str = "stop"
        self.emergency_stopped: bool = False

        # Tracked posture & IMU state
        self.height_z: float = float(self.DEFAULT_HEIGHT_Z)
        self.gait_type: str = "trot"
        self.pace: str = "normal"
        self.imu_enabled: bool = False
        self.roll: float = 0.0
        self.pitch: float = 0.0
        self.yaw: float = 0.0
        self.motor_angles: List[float] = []

        if self.dog is None:
            self._init_hardware()
        else:
            self._refresh_hardware_telemetry()

    def _init_hardware(self) -> None:
        """Initializes serial connection to DOGZILLA motion board using DOGZILLALib."""
        with self._lock:
            try:
                logger.info("Connecting to DOGZILLA hardware on %s...", self.device_endpoint)
                self.dog = DOGZILLA(port=self.device_endpoint)
                self.connected = True
                version = self.dog.read_version()
                self.firmware_version = version if version and version != "Null" else "unknown"
                self._refresh_hardware_telemetry()
                logger.info(
                    "DOGZILLA connected on %s (firmware=%s, battery=%.1f%%)",
                    self.device_endpoint,
                    self.firmware_version,
                    self.battery_level,
                )
            except Exception as exc:
                logger.warning(
                    "Physical DOGZILLA not detected on %s (%s). Controller in offline state.",
                    self.device_endpoint,
                    exc,
                )
                self.dog = None
                self.connected = False

    @classmethod
    def scale_speed_to_step_x(cls, speed: float) -> int:
        """Maps normalized speed (0.0..1.0) to DOGZILLALib move_x step (2..20 mm)."""
        clamped = max(0.0, min(1.0, speed))
        if clamped == 0.0:
            return 0
        return max(2, min(cls.MAX_STEP_X, round(clamped * cls.MAX_STEP_X)))

    @classmethod
    def scale_speed_to_step_y(cls, speed: float) -> int:
        """Maps normalized speed (0.0..1.0) to DOGZILLALib move_y strafe step (2..18 mm)."""
        clamped = max(0.0, min(1.0, speed))
        if clamped == 0.0:
            return 0
        return max(2, min(cls.MAX_STEP_Y, round(clamped * cls.MAX_STEP_Y)))

    @classmethod
    def scale_speed_to_turn(cls, speed: float) -> int:
        """Maps normalized speed (0.0..1.0) to DOGZILLALib turn rate (30..70 deg/s)."""
        clamped = max(0.0, min(1.0, speed))
        if clamped == 0.0:
            return 0
        span = cls.MAX_TURN_STEP - cls.MIN_TURN_STEP
        return max(cls.MIN_TURN_STEP, min(cls.MAX_TURN_STEP, round(cls.MIN_TURN_STEP + clamped * span)))

    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        """Translates BARRY movement command into thread-safe DOGZILLALib serial instructions."""
        action_clean = action.lower().strip()

        # Emergency stop and controlled stop must always be handled first
        if action_clean == "emergency_stop":
            return self.emergency_stop()
        if action_clean == "stop":
            return self.stop()

        if self.emergency_stopped:
            return {
                "success": False,
                "error_code": "ESTOP_ACTIVE",
                "message": "Movement blocked: Emergency stop engaged on DOGZILLA. Reset required.",
                "telemetry": self.get_status(),
            }

        if not self.connected or self.dog is None:
            return {
                "success": False,
                "error_code": "OFFLINE",
                "message": "Cannot move: Physical DOGZILLA hardware is offline.",
                "telemetry": self.get_status(),
            }

        clamped_speed = max(0.0, min(self.max_speed, float(speed)))
        if clamped_speed == 0.0:
            return self.stop()

        with self._lock:
            try:
                if action_clean == "forward":
                    step = self.scale_speed_to_step_x(clamped_speed)
                    self.dog.forward(step)
                elif action_clean in ("backward", "back"):
                    step = self.scale_speed_to_step_x(clamped_speed)
                    self.dog.back(step)
                    action_clean = "backward"
                elif action_clean in ("left", "turn_left"):
                    step = self.scale_speed_to_turn(clamped_speed)
                    self.dog.turnleft(step)
                elif action_clean in ("right", "turn_right"):
                    step = self.scale_speed_to_turn(clamped_speed)
                    self.dog.turnright(step)
                elif action_clean == "strafe_left":
                    step = self.scale_speed_to_step_y(clamped_speed)
                    self.dog.left(step)
                elif action_clean == "strafe_right":
                    step = self.scale_speed_to_step_y(clamped_speed)
                    self.dog.right(step)
                else:
                    return {
                        "success": False,
                        "error_code": "INVALID_ACTION",
                        "message": f"Unknown movement action: '{action}'.",
                        "telemetry": self.get_status(),
                    }

                self.speed = clamped_speed
                self.last_command = action_clean
            except Exception as exc:
                logger.error("DOGZILLA serial move error: %s", exc)
                self.connected = False
                return {
                    "success": False,
                    "error_code": "SERIAL_ERROR",
                    "message": f"DOGZILLA serial failure during '{action_clean}': {exc}",
                    "telemetry": self.get_status(),
                }

        return {
            "success": True,
            "message": f"DOGZILLA executed {action_clean} at speed {clamped_speed} (step={step}).",
            "telemetry": self.get_status(),
        }

    def stop(self) -> Dict[str, Any]:
        """Brings DOGZILLA to a controlled stop (`move_x(0)`, `move_y(0)`, `mark_time(0)`, `turn(0)`)."""
        self.speed = 0.0
        self.last_command = "stop"
        if self.connected and self.dog is not None:
            with self._lock:
                try:
                    self.dog.stop()
                except Exception as exc:
                    logger.error("DOGZILLA stop error: %s", exc)
        return {
            "success": True,
            "message": "DOGZILLA stopped.",
            "telemetry": self.get_status(),
        }

    def emergency_stop(self) -> Dict[str, Any]:
        """Immediately stops motion, resets posture, and unloads all 12 motor servos."""
        self.emergency_stopped = True
        self.speed = 0.0
        self.last_command = "emergency_stop"
        if self.connected and self.dog is not None:
            with self._lock:
                try:
                    self.dog.stop()
                    self.dog.reset()
                    self.dog.unload_allmotor()
                except Exception as exc:
                    logger.error("DOGZILLA emergency_stop serial error: %s", exc)
        return {
            "success": True,
            "message": "DOGZILLA EMERGENCY STOP ACTIVATED! Motors unloaded and locked.",
            "telemetry": self.get_status(),
        }

    def reset_emergency_stop(self) -> Dict[str, Any]:
        """Re-engages servo torque (`load_allmotor`), resets standing posture, and clears E-stop latch."""
        self.emergency_stopped = False
        self.speed = 0.0
        self.last_command = "stop"
        if self.connected and self.dog is not None:
            with self._lock:
                try:
                    self.dog.load_allmotor()
                    self.dog.reset()
                    self.height_z = float(self.DEFAULT_HEIGHT_Z)
                    self.pace = "normal"
                except Exception as exc:
                    logger.error("DOGZILLA reset_emergency_stop error: %s", exc)
        return {
            "success": True,
            "message": "DOGZILLA emergency stop cleared and motors reloaded.",
            "telemetry": self.get_status(),
        }

    def set_posture(
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
        """Controls DOGZILLA body height, camera/body attitude, gait, pace, or obstacle-crossing preset."""
        if self.emergency_stopped:
            return {
                "success": False,
                "error_code": "ESTOP_ACTIVE",
                "message": "Posture change blocked: Emergency stop is active.",
                "telemetry": self.get_status(),
            }
        if not self.connected or self.dog is None:
            return {
                "success": False,
                "error_code": "OFFLINE",
                "message": "Cannot set posture: Physical DOGZILLA hardware is offline.",
                "telemetry": self.get_status(),
            }

        with self._lock:
            try:
                if preset:
                    p_clean = preset.strip().lower()
                    if p_clean == "obstacle_crossing":
                        # Matches joystick_dogzilla.py __obstacle_crossing()
                        self.dog.gait_type("high_walk")
                        self.dog.pace("slow")
                        self.dog.translation("z", 95)
                        self.gait_type = "high_walk"
                        self.pace = "slow"
                        self.height_z = 95.0
                    elif p_clean == "crouch":
                        self.dog.pace("slow")
                        self.dog.translation("z", self.MIN_HEIGHT_Z)
                        self.pace = "slow"
                        self.height_z = float(self.MIN_HEIGHT_Z)
                    elif p_clean in ("stand", "reset", "default"):
                        self.dog.reset()
                        self.dog.pace("normal")
                        self.dog.translation("z", self.DEFAULT_HEIGHT_Z)
                        self.gait_type = "trot"
                        self.pace = "normal"
                        self.height_z = float(self.DEFAULT_HEIGHT_Z)
                        self.roll = 0.0
                        self.pitch = 0.0
                        self.yaw = 0.0
                    else:
                        return {
                            "success": False,
                            "message": f"Unknown posture preset '{preset}'.",
                            "telemetry": self.get_status(),
                        }

                if height_z is not None:
                    clamped_z = max(self.MIN_HEIGHT_Z, min(self.MAX_HEIGHT_Z, round(height_z)))
                    self.dog.translation("z", clamped_z)
                    self.height_z = float(clamped_z)

                if roll is not None:
                    lim_r = PARAM["ATTITUDE_LIMIT"][0]
                    clamped_r = max(-lim_r, min(lim_r, float(roll)))
                    self.dog.attitude("r", clamped_r)
                    self.roll = clamped_r

                if pitch is not None:
                    lim_p = PARAM["ATTITUDE_LIMIT"][1]
                    clamped_p = max(-lim_p, min(lim_p, float(pitch)))
                    self.dog.attitude("p", clamped_p)
                    self.pitch = clamped_p

                if yaw is not None:
                    lim_y = PARAM["ATTITUDE_LIMIT"][2]
                    clamped_y = max(-lim_y, min(lim_y, float(yaw)))
                    self.dog.attitude("y", clamped_y)
                    self.yaw = clamped_y

                if gait_type is not None:
                    g_clean = gait_type.strip().lower()
                    if g_clean not in ("trot", "walk", "high_walk"):
                        return {
                            "success": False,
                            "message": f"Invalid gait_type '{gait_type}'. Must be trot, walk, or high_walk.",
                            "telemetry": self.get_status(),
                        }
                    self.dog.gait_type(g_clean)
                    self.gait_type = g_clean

                if pace is not None:
                    pace_clean = pace.strip().lower()
                    if pace_clean not in ("slow", "normal", "high"):
                        return {
                            "success": False,
                            "message": f"Invalid pace '{pace}'. Must be slow, normal, or high.",
                            "telemetry": self.get_status(),
                        }
                    self.dog.pace(pace_clean)
                    self.pace = pace_clean

                if imu_enabled is not None:
                    self.dog.imu(1 if imu_enabled else 0)
                    self.imu_enabled = bool(imu_enabled)

                self.last_command = f"posture:{preset or 'custom'}"
            except Exception as exc:
                logger.error("DOGZILLA set_posture error: %s", exc)
                return {
                    "success": False,
                    "message": f"DOGZILLA posture command failed: {exc}",
                    "telemetry": self.get_status(),
                }

        return {
            "success": True,
            "message": "DOGZILLA posture updated.",
            "telemetry": self.get_status(),
        }

    def _refresh_hardware_telemetry(self) -> None:
        """Polls battery and IMU roll/pitch/yaw from DOGZILLALib under serial lock."""
        if not self.connected or self.dog is None:
            return
        with self._lock:
            try:
                bat = self.dog.read_battery()
                if isinstance(bat, (int, float)) and 0 <= bat <= 100:
                    self.battery_level = float(bat)
                self.roll = float(self.dog.read_roll())
                self.pitch = float(self.dog.read_pitch())
                self.yaw = float(self.dog.read_yaw())
            except Exception as exc:
                logger.debug("Telemetry poll skipped: %s", exc)

    def get_status(self) -> Dict[str, Any]:
        """Returns hardware telemetry, IMU orientation, and posture settings."""
        if self.connected and self.dog is not None:
            self._refresh_hardware_telemetry()
        return {
            "connected": self.connected,
            "mode": self.mode,
            "firmware_version": self.firmware_version,
            "battery_level": self.battery_level,
            "position": {"x": 0.0, "y": 0.0, "heading": round(self.yaw % 360.0, 1)},
            "imu": {
                "roll": self.roll,
                "pitch": self.pitch,
                "yaw": self.yaw,
                "imu_stabilization": self.imu_enabled,
            },
            "posture": {
                "height_z": self.height_z,
                "gait_type": self.gait_type,
                "pace": self.pace,
            },
            "speed": self.speed,
            "last_command": self.last_command,
            "emergency_stopped": self.emergency_stopped,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
