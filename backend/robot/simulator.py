"""Robot Simulator for BARRY.

A fake DOGZILLA that follows ``RobotInterface`` but only updates numbers in
memory (position, heading, battery). Use it to develop and test the backend,
the Pi Robot API, and the Quest app without the physical robot.

Selected with ``ROBOT_MODE=simulation``.

Motion model (one ``move()`` call = one "tick"):
    forward / backward  move ``speed * step_m`` meters along the current heading
    left / right        turn in place by ``turn_deg`` degrees (not a sideways step)

Heading is in degrees: 0 = East (+x), 90 = North (+y).

This module must stay self-contained: it imports nothing from the laptop-only
parts of the backend (database, missions, models), so it can run on the Pi.
"""

from datetime import datetime, timezone
import math
import threading
from typing import Any, Dict

from backend.robot.interface import RobotInterface

# Battery cost per tick, in percent.
_DRAIN_PER_STEP = 0.1
_DRAIN_PER_TURN = 0.08

_MOVE_ACTIONS = {"forward", "backward", "left", "right"}


class RobotSimulator(RobotInterface):
    """Simulates DOGZILLA quadruped movement and telemetry."""

    def __init__(
        self,
        battery_level: float = 98.5,
        step_m: float = 1.0,
        turn_deg: float = 15.0,
    ) -> None:
        """
        :param battery_level: Starting battery percentage (0-100).
        :param step_m: Meters travelled per forward/backward tick at speed 1.0.
        :param turn_deg: Degrees turned per left/right tick.
        """
        self.connected: bool = True
        self.mode: str = "simulation"
        self.battery_level: float = max(0.0, min(100.0, battery_level))
        self.step_m = step_m
        self.turn_deg = turn_deg

        self.x: float = 0.0
        self.y: float = 0.0
        self.heading: float = 0.0
        self.speed: float = 0.0
        self.last_command: str = "stop"
        self.emergency_stopped: bool = False

        # The Pi Robot API calls the robot from request handlers and from its
        # watchdog task at the same time; the lock keeps state consistent.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # RobotInterface
    # ------------------------------------------------------------------

    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        """Executes one simulated movement tick.

        Also accepts ``"stop"`` and ``"emergency_stop"`` as actions, because
        ``/api/robot/command`` and the ``/ws`` channel send them through here.
        """
        if not isinstance(action, str):
            return self._result(False, f"Invalid action: {action!r}. Expected a string.")
        action_clean = action.lower().strip()

        # Stop commands are always accepted, even during emergency stop.
        if action_clean == "stop":
            return self.stop()
        if action_clean == "emergency_stop":
            return self.emergency_stop()

        with self._lock:
            if self.emergency_stopped:
                return self._result(False, "Movement blocked: Emergency stop is active. Reset required.")

            if action_clean not in _MOVE_ACTIONS:
                return self._result(False, f"Unknown movement action: '{action}'.")

            try:
                speed_value = float(speed)
            except (TypeError, ValueError):
                return self._result(False, f"Invalid speed: {speed!r}. Expected a number between 0.0 and 1.0.")
            if math.isnan(speed_value):
                return self._result(False, "Invalid speed: NaN.")
            clamped_speed = max(0.0, min(1.0, speed_value))

            if self.battery_level <= 0.0:
                self.speed = 0.0
                return self._result(False, "Movement blocked: Battery depleted.")

            if action_clean in ("forward", "backward"):
                direction = 1.0 if action_clean == "forward" else -1.0
                distance = direction * clamped_speed * self.step_m
                rad = math.radians(self.heading)
                self.x = round(self.x + distance * math.cos(rad), 3)
                self.y = round(self.y + distance * math.sin(rad), 3)
                self._drain_battery(_DRAIN_PER_STEP)
            else:
                direction = 1.0 if action_clean == "left" else -1.0
                self.heading = round((self.heading + direction * self.turn_deg) % 360.0, 1)
                self._drain_battery(_DRAIN_PER_TURN)

            self.speed = clamped_speed
            self.last_command = action_clean
            return self._result(True, f"Simulated movement: {action_clean} at speed {clamped_speed}.")

    def stop(self) -> Dict[str, Any]:
        """Brings the simulated robot to a controlled stop."""
        with self._lock:
            self.speed = 0.0
            # Don't overwrite the e-stop marker: the status should keep showing
            # why the robot is locked until it is reset.
            if not self.emergency_stopped:
                self.last_command = "stop"
            return self._result(True, "Robot stopped.")

    def emergency_stop(self) -> Dict[str, Any]:
        """Halts the robot and latches the emergency stop until reset."""
        with self._lock:
            self.speed = 0.0
            self.last_command = "emergency_stop"
            self.emergency_stopped = True
            return self._result(True, "EMERGENCY STOP ACTIVATED! Robot immobilized.")

    def reset_emergency_stop(self) -> Dict[str, Any]:
        """Clears the emergency stop latch."""
        with self._lock:
            self.emergency_stopped = False
            self.speed = 0.0
            self.last_command = "stop"
            return self._result(True, "Emergency stop cleared. Locomotion restored.")

    def get_status(self) -> Dict[str, Any]:
        """Returns the current state of the simulated robot."""
        with self._lock:
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _drain_battery(self, amount: float) -> None:
        """Simulates battery depletion during operations."""
        self.battery_level = max(0.0, round(self.battery_level - amount, 2))

    def _result(self, success: bool, message: str) -> Dict[str, Any]:
        """Builds the standard command response."""
        return {
            "success": success,
            "message": message,
            "telemetry": self.get_status(),
        }
