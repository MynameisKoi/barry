"""Robot Interface Specification for BARRY.

Defines the common contract for physical robots (DOGZILLA via DOGZILLALib),
remote Raspberry Pi clients, and kinematic simulators.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class RobotInterface(ABC):
    """Abstract base class that decouples the backend from physical hardware."""

    @abstractmethod
    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        """Executes a movement command ('forward', 'backward', 'left', 'right', 'strafe_left', 'strafe_right').

        :param action: Direction of movement.
        :param speed: Speed multiplier between 0.0 and 1.0.
        :return: Execution status and updated telemetry.
        """
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> Dict[str, Any]:
        """Brings the robot to a controlled stop.

        :return: Execution status and updated telemetry.
        """
        raise NotImplementedError

    @abstractmethod
    def emergency_stop(self) -> Dict[str, Any]:
        """Immediately halts all locomotion and locks movement until reset.

        :return: Emergency stop status.
        """
        raise NotImplementedError

    @abstractmethod
    def reset_emergency_stop(self) -> Dict[str, Any]:
        """Clears the emergency stop latch to resume normal operations.

        :return: Reset status.
        """
        raise NotImplementedError

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Returns current telemetry, operating mode, IMU attitude, and battery status.

        :return: Dictionary containing robot status.
        """
        raise NotImplementedError

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
        """Configures body height (75-115mm), attitude (roll/pitch/yaw), gait, pace, or IMU stabilization."""
        return {
            "success": False,
            "message": "Posture control not implemented on this controller.",
            "telemetry": self.get_status(),
        }
