"""Laptop-side robot HTTP routes (``/api/robot/*``), mounted by ``backend.main``.

The router never owns the robot: ``get_robot`` is called per request, so the
backend can build the robot in its lifespan and tests can swap it. Event
logging and WebSocket broadcast are injected so this module has no import-time
dependency on ``backend.main``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict

from fastapi import APIRouter, HTTPException, status

from backend.models import RobotCommandRequest, RobotStatusResponse
from backend.robot.interface import RobotInterface
from backend.robot.schemas import PostureUpdateRequest

LogEvent = Callable[..., Any]
Broadcast = Callable[[Dict[str, Any]], Awaitable[Any]]


def create_robot_router(
    get_robot: Callable[[], RobotInterface],
    log_event: LogEvent,
    broadcast: Broadcast,
) -> APIRouter:
    """Build the ``/api/robot`` router.

    Args:
        get_robot: Zero-arg provider of the shared robot; may raise
            ``HTTPException(503)`` before the backend has started it.
        log_event: ``log_event(event_type=..., description=...)`` for the
            mission audit trail.
        broadcast: Coroutine that sends a message to connected WebSocket clients.
    """
    router = APIRouter(prefix="/api/robot", tags=["Robot"])

    async def _broadcast_status() -> None:
        await broadcast({"type": "robot_status", "telemetry": get_robot().get_status()})

    @router.get("/status", response_model=RobotStatusResponse)
    async def get_robot_status():
        """Returns robot connection, battery, coordinates, and operating status."""
        return get_robot().get_status()

    @router.post("/command")
    async def send_robot_command(command: RobotCommandRequest):
        """Sends movement command (forward, backward, left, right, stop, emergency_stop)."""
        action = command.action.lower().strip()
        result = await asyncio.to_thread(get_robot().move, action=action, speed=command.speed)

        event_type = "emergency_stop" if action == "emergency_stop" else "robot_command"
        log_event(
            event_type=event_type,
            description=f"Action: {action}, Speed: {command.speed}, Success: {result['success']}",
        )
        await broadcast(
            {
                "type": "robot_command_feedback",
                "action": action,
                "speed": command.speed,
                "success": result["success"],
                "telemetry": result.get("telemetry"),
            }
        )
        if not result["success"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["message"])
        return result

    @router.post("/reset-emergency")
    async def reset_emergency_stop():
        """Clears the emergency stop lock on the robot."""
        result = await asyncio.to_thread(get_robot().reset_emergency_stop)
        log_event(
            event_type="emergency_stop_reset",
            description="Emergency stop lock cleared by operator.",
        )
        await _broadcast_status()
        return result

    @router.post("/stop")
    async def stop_robot_endpoint():
        """Brings the robot to a controlled stop."""
        result = await asyncio.to_thread(get_robot().stop)
        log_event(event_type="robot_command", description="Action: stop (controlled stop)")
        await _broadcast_status()
        return result

    @router.post("/posture")
    async def set_robot_posture(payload: PostureUpdateRequest):
        """Updates DOGZILLA body height (z), attitude (roll/pitch/yaw), gait_type, pace, or preset."""
        result = await asyncio.to_thread(
            get_robot().set_posture,
            height_z=payload.height_z,
            roll=payload.roll,
            pitch=payload.pitch,
            yaw=payload.yaw,
            gait_type=payload.gait_type,
            pace=payload.pace,
            imu_enabled=payload.imu_enabled,
            preset=payload.preset,
        )
        log_event(
            event_type="robot_posture",
            description=f"Posture update: {payload.model_dump(exclude_none=True)}, Success: {result.get('success')}",
        )
        await _broadcast_status()
        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("message", "Posture update failed."),
            )
        return result

    return router
