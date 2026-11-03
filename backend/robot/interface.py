"""Robot Interface Specification for BARRY.

Defines the common contract for both physical robots (DOGZILLA) and simulators.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class RobotInterface(ABC):
    """Abstract base class that decouples the backend from physical hardware."""

    @abstractmethod
    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        """Executes a movement command ('forward', 'backward', 'left', 'right').

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
        """Returns current telemetry, operating mode, and battery status.

        :return: Dictionary containing robot status.
        """
        raise NotImplementedError
