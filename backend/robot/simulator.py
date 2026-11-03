"""Robot Simulator for BARRY.

Provides complete kinematics, status reporting, and safety mechanisms
for testing before physical DOGZILLA hardware arrives.
"""

from datetime import datetime, timezone
import math
from typing import Any, Dict
from backend.robot.interface import RobotInterface


class RobotSimulator(RobotInterface):
    """Simulates DOGZILLA quadruped movement and telemetry."""

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

    def _drain_battery(self, amount: float = 0.05) -> None:
        """Simulates battery depletion during operations."""
        self.battery_level = max(0.0, round(self.battery_level - amount, 2))

    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        """Executes simulated movement."""
        if self.emergency_stopped:
            return {
                "success": False,
                "message": "Movement blocked: Emergency stop is active. Reset required.",
                "telemetry": self.get_status(),
            }

        action_clean = action.lower().strip()
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

        elif action_clean == "backward":
            rad = math.radians(self.heading)
            self.x = round(self.x - step_distance * math.cos(rad), 3)
            self.y = round(self.y - step_distance * math.sin(rad), 3)
            self.speed = clamped_speed
            self.last_command = "backward"
            self._drain_battery(0.1)

        elif action_clean == "left":
            self.heading = round((self.heading + turn_angle) % 360.0, 1)
            self.speed = clamped_speed
            self.last_command = "left"
            self._drain_battery(0.08)

        elif action_clean == "right":
            self.heading = round((self.heading - turn_angle) % 360.0, 1)
            self.speed = clamped_speed
            self.last_command = "right"
            self._drain_battery(0.08)

        elif action_clean == "stop":
            return self.stop()

        elif action_clean == "emergency_stop":
            return self.emergency_stop()

        else:
            return {
                "success": False,
                "message": f"Unknown movement action: '{action}'.",
                "telemetry": self.get_status(),
            }

        return {
            "success": True,
            "message": f"Simulated movement: {action_clean} at speed {clamped_speed}.",
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
            "speed": self.speed,
            "last_command": self.last_command,
            "emergency_stopped": self.emergency_stopped,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
