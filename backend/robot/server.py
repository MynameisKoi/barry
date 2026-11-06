"""DOGZILLA Raspberry Pi Robot Control API (`backend/robot/server.py`).

Runs on the DOGZILLA Raspberry Pi (port 8100) as the single owner of `/dev/ttyAMA0`:
- Authenticates requests via `X-Robot-Key` (`ROBOT_API_KEY` or `ROBOT_EMERGENCY_KEY`).
- Enforces `ROBOT_MAX_SPEED` (and lower `ROBOT_EMERGENCY_MAX_SPEED` in emergency direct mode).
- Runs a background Watchdog auto-stop loop (`ROBOT_WATCHDOG_MS`, default 500 ms) that
  automatically halts the robot if movement commands stop arriving.
"""

import asyncio
from contextlib import asynccontextmanager
import logging
import time
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from backend.models import RobotCommandRequest
from backend.robot.config import RobotConfig, get_robot_config
from backend.robot.factory import create_robot
from backend.robot.interface import RobotInterface

logger = logging.getLogger("barry.robot.server")


class PostureUpdateRequest(BaseModel):
    """Request schema for updating DOGZILLA body height, IMU attitude, gait, or pace."""

    height_z: Optional[float] = Field(default=None, ge=75.0, le=115.0)
    roll: Optional[float] = Field(default=None, ge=-20.0, le=20.0)
    pitch: Optional[float] = Field(default=None, ge=-15.0, le=15.0)
    yaw: Optional[float] = Field(default=None, ge=-11.0, le=11.0)
    gait_type: Optional[str] = Field(default=None, description="trot, walk, or high_walk")
    pace: Optional[str] = Field(default=None, description="slow, normal, or high")
    imu_enabled: Optional[bool] = None
    preset: Optional[str] = Field(
        default=None, description="stand, crouch, or obstacle_crossing"
    )


class WatchdogController:
    """Tracks last movement command timestamp and auto-stops the robot if the link drops."""

    def __init__(self, robot: RobotInterface, timeout_ms: int = 500) -> None:
        self.robot = robot
        self.timeout_sec = max(0.1, timeout_ms / 1000.0)
        self.last_command_ts: float = 0.0
        self.is_moving: bool = False

    def feed(self, action: str) -> None:
        action_clean = action.lower().strip()
        if action_clean in ("stop", "emergency_stop"):
            self.is_moving = False
        else:
            self.last_command_ts = time.monotonic()
            self.is_moving = True

    def check_and_stop_if_expired(self) -> bool:
        """Stops the robot if `timeout_sec` elapsed since the last movement command."""
        if self.is_moving and (time.monotonic() - self.last_command_ts) > self.timeout_sec:
            logger.warning(
                "Robot watchdog expired (>%.0f ms without command) — auto-stopping DOGZILLA.",
                self.timeout_sec * 1000.0,
            )
            self.robot.stop()
            self.is_moving = False
            return True
        return False


pi_config: RobotConfig = get_robot_config()
pi_robot: RobotInterface = create_robot(config=pi_config)
watchdog = WatchdogController(pi_robot, timeout_ms=pi_config.ROBOT_WATCHDOG_MS)


async def _watchdog_loop() -> None:
    try:
        while True:
            await asyncio.sleep(0.05)
            watchdog.check_and_stop_if_expired()
    except asyncio.CancelledError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_watchdog_loop())
    yield
    task.cancel()
    pi_robot.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="BARRY Pi Robot Control API",
    description="Dedicated hardware control service for DOGZILLA S1/S2 on Raspberry Pi",
    version="1.0.0",
    lifespan=lifespan,
)


def verify_robot_key(
    x_robot_key: Optional[str] = Header(default=None, alias="X-Robot-Key"),
) -> str:
    """Validates X-Robot-Key if ROBOT_API_KEY is configured."""
    expected_primary = pi_config.ROBOT_API_KEY
    expected_emergency = pi_config.ROBOT_EMERGENCY_KEY

    # If no key is configured (e.g., local simulation/dev), allow access as 'laptop'
    if not expected_primary and not expected_emergency:
        return "laptop"

    if x_robot_key and expected_primary and x_robot_key == expected_primary:
        return "laptop"
    if x_robot_key and expected_emergency and x_robot_key == expected_emergency:
        return "emergency"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing X-Robot-Key header.",
    )


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    status_info = pi_robot.get_status()
    return {
        "status": "ok",
        "connected": status_info.get("connected", False),
        "mode": status_info.get("mode", "unknown"),
        "emergency_stopped": status_info.get("emergency_stopped", False),
    }


@app.get("/api/robot/status")
async def get_status(_caller: str = Depends(verify_robot_key)) -> Dict[str, Any]:
    return pi_robot.get_status()


@app.post("/api/robot/command")
async def send_command(
    command: RobotCommandRequest,
    caller: str = Depends(verify_robot_key),
) -> Dict[str, Any]:
    max_allowed = (
        pi_config.ROBOT_EMERGENCY_MAX_SPEED
        if caller == "emergency"
        else pi_config.ROBOT_MAX_SPEED
    )
    capped_speed = min(command.speed, max_allowed)
    result = pi_robot.move(action=command.action, speed=capped_speed)
    if result.get("success"):
        watchdog.feed(command.action)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Robot command failed."),
        )
    return result


@app.post("/api/robot/stop")
async def stop_robot(_caller: str = Depends(verify_robot_key)) -> Dict[str, Any]:
    watchdog.feed("stop")
    return pi_robot.stop()


@app.post("/api/robot/emergency-stop")
async def emergency_stop_robot(_caller: str = Depends(verify_robot_key)) -> Dict[str, Any]:
    watchdog.feed("emergency_stop")
    return pi_robot.emergency_stop()


@app.post("/api/robot/reset-emergency")
async def reset_emergency(_caller: str = Depends(verify_robot_key)) -> Dict[str, Any]:
    watchdog.feed("stop")
    return pi_robot.reset_emergency_stop()


@app.post("/api/robot/posture")
async def update_posture(
    payload: PostureUpdateRequest,
    _caller: str = Depends(verify_robot_key),
) -> Dict[str, Any]:
    result = pi_robot.set_posture(
        height_z=payload.height_z,
        roll=payload.roll,
        pitch=payload.pitch,
        yaw=payload.yaw,
        gait_type=payload.gait_type,
        pace=payload.pace,
        imu_enabled=payload.imu_enabled,
        preset=payload.preset,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Posture update failed."),
        )
    return result
