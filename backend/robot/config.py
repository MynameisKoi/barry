"""Configuration for BARRY Robot Control & Pi Gateway (`backend/robot/config.py`)."""

import os
from typing import Any
from dotenv import load_dotenv

load_dotenv()


class RobotConfig:
    """Environment-driven configuration for laptop, simulator, and Raspberry Pi DOGZILLA controller."""

    def __init__(self, **overrides: Any) -> None:
        self.ROBOT_MODE: str = os.getenv("ROBOT_MODE", "simulation").strip().lower()
        self.ROBOT_API_KEY: str = os.getenv("ROBOT_API_KEY", "").strip()
        self.ROBOT_EMERGENCY_KEY: str = os.getenv("ROBOT_EMERGENCY_KEY", "").strip()
        self.ROBOT_REMOTE_URL: str = os.getenv(
            "ROBOT_REMOTE_URL", "http://barry-pi.local:8100"
        ).rstrip("/")
        self.ROBOT_REMOTE_TIMEOUT: float = float(
            os.getenv("ROBOT_REMOTE_TIMEOUT", "0.5")
        )
        self.DOGZILLA_PORT: str = os.getenv("DOGZILLA_PORT", "/dev/ttyAMA0").strip()
        self.DOGZILLA_BAUD: int = int(os.getenv("DOGZILLA_BAUD", "115200"))
        self.ROBOT_MAX_SPEED: float = max(
            0.05, min(1.0, float(os.getenv("ROBOT_MAX_SPEED", "0.6")))
        )
        self.ROBOT_EMERGENCY_MAX_SPEED: float = max(
            0.05, min(1.0, float(os.getenv("ROBOT_EMERGENCY_MAX_SPEED", "0.35")))
        )
        self.ROBOT_WATCHDOG_MS: int = max(
            100, int(os.getenv("ROBOT_WATCHDOG_MS", "500"))
        )

        for key, value in overrides.items():
            setattr(self, key.upper(), value)


def get_robot_config() -> RobotConfig:
    return RobotConfig()
