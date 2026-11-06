"""Robot Simulator for BARRY (`backend/robot/simulator.py`).

Provides complete kinematics, IMU/posture simulation, status reporting,
and safety mechanisms matching DOGZILLALib v2.0.7 capabilities.
"""

from datetime import datetime, timezone
import math
from typing import Any, Dict, Optional
from backend.robot.DOGZILLALib import PARAM
from backend.robot.interface import RobotInterface


class RobotSimulator(RobotInterface):
    """Simulates DOGZILLA quadruped movement, posture, IMU, and telemetry."""

    def __init__(self) -> None:
        self.connected: bool = True
        self.mode: str = "simulation"
        self.battery_level: float = 98.5
        self.x: float = 0.0
        self.y: float = 0.0
        self.heading: float = 0.0  # degrees: 0 = East, 90 = North
        self.speed: float = 0.0
        self.last_command: str = "stop"
        self.emergency_stopped: bool = False

        # Simulated DOGZILLALib posture & IMU state
        self.height_z: float = 105.0
        self.gait_type: str = "trot"
        self.pace: str = "normal"
        self.imu_enabled: bool = False
        self.roll: float = 0.0
        self.pitch: float = 0.0
        self.yaw: float = 0.0

    def _drain_battery(self, amount: float = 0.05) -> None:
        """Simulates battery depletion during operations."""
        self.battery_level = max(0.0, round(self.battery_level - amount, 2))

    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        """Executes simulated movement."""
        action_clean = action.lower().strip()

        if action_clean == "emergency_stop":
            return self.emergency_stop()
        if action_clean == "stop":
            return self.stop()

        if self.emergency_stopped:
            return {
                "success": False,
                "error_code": "ESTOP_ACTIVE",
                "message": "Movement blocked: Emergency stop is active. Reset required.",
                "telemetry": self.get_status(),
            }

        clamped_speed = max(0.0, min(1.0, speed))
        step_distance = clamped_speed * 1.0  # meters per command tick
        turn_angle = 15.0  # degrees per turn tick

        if action_clean == "forward":
            rad = math.radians(self.heading)
            self.x = round(self.x + step_distance * math.cos(rad), 3)
            self.y = round(self.y + step_distance * math.sin(rad), 3)
            self.speed = clamped_speed
            self.last_command = "forward"
            self._drain_battery(0.1)

        elif action_clean in ("backward", "back"):
            rad = math.radians(self.heading)
            self.x = round(self.x - step_distance * math.cos(rad), 3)
            self.y = round(self.y - step_distance * math.sin(rad), 3)
            self.speed = clamped_speed
            self.last_command = "backward"
            self._drain_battery(0.1)

        elif action_clean in ("left", "turn_left"):
            self.heading = round((self.heading + turn_angle) % 360.0, 1)
            self.speed = clamped_speed
            self.last_command = "left"
            self._drain_battery(0.08)

        elif action_clean in ("right", "turn_right"):
            self.heading = round((self.heading - turn_angle) % 360.0, 1)
            self.speed = clamped_speed
            self.last_command = "right"
            self._drain_battery(0.08)

        elif action_clean == "strafe_left":
            rad = math.radians((self.heading + 90.0) % 360.0)
            self.x = round(self.x + step_distance * math.cos(rad), 3)
            self.y = round(self.y + step_distance * math.sin(rad), 3)
            self.speed = clamped_speed
            self.last_command = "strafe_left"
            self._drain_battery(0.09)

        elif action_clean == "strafe_right":
            rad = math.radians((self.heading - 90.0) % 360.0)
            self.x = round(self.x + step_distance * math.cos(rad), 3)
            self.y = round(self.y + step_distance * math.sin(rad), 3)
            self.speed = clamped_speed
            self.last_command = "strafe_right"
            self._drain_battery(0.09)

        else:
            return {
                "success": False,
                "error_code": "INVALID_ACTION",
                "message": f"Unknown movement action: '{action}'.",
                "telemetry": self.get_status(),
            }

        return {
            "success": True,
            "message": f"Simulated movement: {self.last_command} at speed {clamped_speed}.",
            "telemetry": self.get_status(),
        }

    def stop(self) -> Dict[str, Any]:
        """Simulates robot halt."""
        self.speed = 0.0
        self.last_command = "stop"
        return {
            "success": True,
            "message": "Robot stopped.",
            "telemetry": self.get_status(),
        }

    def emergency_stop(self) -> Dict[str, Any]:
        """Triggers emergency stop."""
        self.speed = 0.0
        self.last_command = "emergency_stop"
        self.emergency_stopped = True
        return {
            "success": True,
            "message": "EMERGENCY STOP ACTIVATED! Robot immobilized.",
            "telemetry": self.get_status(),
        }

    def reset_emergency_stop(self) -> Dict[str, Any]:
        """Clears emergency stop lock."""
        self.emergency_stopped = False
        self.last_command = "stop"
        return {
            "success": True,
            "message": "Emergency stop cleared. Locomotion restored.",
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
        """Simulates DOGZILLALib body height, attitude, gait, pace, and presets."""
        if self.emergency_stopped:
            return {
                "success": False,
                "error_code": "ESTOP_ACTIVE",
                "message": "Posture change blocked: Emergency stop is active.",
                "telemetry": self.get_status(),
            }

        if preset:
            p_clean = preset.strip().lower()
            if p_clean == "obstacle_crossing":
                self.gait_type = "high_walk"
                self.pace = "slow"
                self.height_z = 95.0
            elif p_clean == "crouch":
                self.pace = "slow"
                self.height_z = 75.0
            elif p_clean in ("stand", "reset", "default"):
                self.gait_type = "trot"
                self.pace = "normal"
                self.height_z = 105.0
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
            self.height_z = float(max(75, min(115, round(height_z))))
        if roll is not None:
            lim_r = PARAM["ATTITUDE_LIMIT"][0]
            self.roll = max(-lim_r, min(lim_r, float(roll)))
        if pitch is not None:
            lim_p = PARAM["ATTITUDE_LIMIT"][1]
            self.pitch = max(-lim_p, min(lim_p, float(pitch)))
        if yaw is not None:
            lim_y = PARAM["ATTITUDE_LIMIT"][2]
            self.yaw = max(-lim_y, min(lim_y, float(yaw)))
        if gait_type is not None:
            g_clean = gait_type.strip().lower()
            if g_clean not in ("trot", "walk", "high_walk"):
                return {
                    "success": False,
                    "message": f"Invalid gait_type '{gait_type}'. Must be trot, walk, or high_walk.",
                    "telemetry": self.get_status(),
                }
            self.gait_type = g_clean
        if pace is not None:
            pace_clean = pace.strip().lower()
            if pace_clean not in ("slow", "normal", "high"):
                return {
                    "success": False,
                    "message": f"Invalid pace '{pace}'. Must be slow, normal, or high.",
                    "telemetry": self.get_status(),
                }
            self.pace = pace_clean
        if imu_enabled is not None:
            self.imu_enabled = bool(imu_enabled)

        self.last_command = f"posture:{preset or 'custom'}"
        return {
            "success": True,
            "message": "Simulated DOGZILLA posture updated.",
            "telemetry": self.get_status(),
        }

    def get_status(self) -> Dict[str, Any]:
        """Returns the current state of the simulated robot."""
        return {
            "connected": self.connected,
            "mode": self.mode,
            "battery_level": self.battery_level,
            "position": {
                "x": self.x,
                "y": self.y,
                "heading": self.heading,
            },
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
