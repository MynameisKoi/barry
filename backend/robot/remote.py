"""Laptop-side HTTP Client for the Raspberry Pi DOGZILLA Robot API (`backend/robot/remote.py`).

Implements `RobotInterface` by forwarding commands over HTTP to `backend.robot.server:app`
running on the DOGZILLA Raspberry Pi (`ROBOT_REMOTE_URL`), authenticated via `X-Robot-Key`.
Reports `connected: False` gracefully when the Pi is unreachable.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional

import httpx

from backend.robot.config import get_robot_config
from backend.robot.interface import RobotInterface

logger = logging.getLogger("barry.robot.remote")


class RemoteRobotClient(RobotInterface):
    """Forwards RobotInterface calls over HTTP to the Raspberry Pi Robot API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        cfg = get_robot_config()
        self.base_url = (base_url or cfg.ROBOT_REMOTE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else cfg.ROBOT_API_KEY
        self.timeout = timeout if timeout is not None else cfg.ROBOT_REMOTE_TIMEOUT
        self.last_command: str = "stop"
        self.emergency_stopped: bool = False

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Robot-Key"] = self.api_key
        return headers

    def _offline_telemetry(self, detail: str = "Raspberry Pi Robot API unreachable") -> Dict[str, Any]:
        return {
            "connected": False,
            "mode": "remote",
            "battery_level": 0.0,
            "position": {"x": 0.0, "y": 0.0, "heading": 0.0},
            "imu": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "imu_stabilization": False},
            "posture": {"height_z": 105.0, "gait_type": "trot", "pace": "normal"},
            "speed": 0.0,
            "last_command": self.last_command,
            "emergency_stopped": self.emergency_stopped,
            "error": detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        action_clean = action.lower().strip()
        self.last_command = action_clean
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/robot/command",
                    headers=self._headers(),
                    json={"action": action_clean, "speed": speed},
                )
            if resp.status_code == 200:
                return resp.json()
            detail = resp.json().get("detail", f"HTTP {resp.status_code}")
            return {
                "success": False,
                "message": str(detail),
                "telemetry": self.get_status(),
            }
        except Exception as exc:
            logger.warning("RemoteRobotClient move('%s') failed: %s", action_clean, exc)
            return {
                "success": False,
                "error_code": "OFFLINE",
                "message": f"Robot Pi API unreachable: {exc}",
                "telemetry": self._offline_telemetry(str(exc)),
            }

    def stop(self) -> Dict[str, Any]:
        self.last_command = "stop"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/robot/stop",
                    headers=self._headers(),
                )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("RemoteRobotClient stop() failed: %s", exc)
        return {
            "success": False,
            "error_code": "OFFLINE",
            "message": "Robot Pi API unreachable during stop.",
            "telemetry": self._offline_telemetry(),
        }

    def emergency_stop(self) -> Dict[str, Any]:
        self.emergency_stopped = True
        self.last_command = "emergency_stop"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/robot/emergency-stop",
                    headers=self._headers(),
                )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("RemoteRobotClient emergency_stop() failed: %s", exc)
        return {
            "success": False,
            "error_code": "OFFLINE",
            "message": "Local emergency stop latched; Pi API was unreachable.",
            "telemetry": self._offline_telemetry(),
        }

    def reset_emergency_stop(self) -> Dict[str, Any]:
        self.emergency_stopped = False
        self.last_command = "stop"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/robot/reset-emergency",
                    headers=self._headers(),
                )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("RemoteRobotClient reset_emergency_stop() failed: %s", exc)
        return {
            "success": False,
            "error_code": "OFFLINE",
            "message": "Robot Pi API unreachable during emergency reset.",
            "telemetry": self._offline_telemetry(),
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
        payload = {
            k: v
            for k, v in {
                "height_z": height_z,
                "roll": roll,
                "pitch": pitch,
                "yaw": yaw,
                "gait_type": gait_type,
                "pace": pace,
                "imu_enabled": imu_enabled,
                "preset": preset,
            }.items()
            if v is not None
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/robot/posture",
                    headers=self._headers(),
                    json=payload,
                )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.warning("RemoteRobotClient set_posture() failed: %s", exc)
        return {
            "success": False,
            "error_code": "OFFLINE",
            "message": "Robot Pi API unreachable during posture update.",
            "telemetry": self._offline_telemetry(),
        }

    def get_status(self) -> Dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(
                    f"{self.base_url}/api/robot/status",
                    headers=self._headers(),
                )
            if resp.status_code == 200:
                data = resp.json()
                self.emergency_stopped = bool(data.get("emergency_stopped", False))
                return data
        except Exception as exc:
            logger.debug("RemoteRobotClient get_status offline: %s", exc)
        return self._offline_telemetry()
