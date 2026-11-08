"""Quest-facing ``/api/v1`` robot routes relayed by the laptop to the Pi."""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, Request, Response

from backend.robot import protocol
from backend.robot.relay import RobotApiRelay

logger = logging.getLogger(__name__)
LogEvent = Callable[..., Any]


def create_robot_relay_router(
    get_relay: Callable[[], RobotApiRelay],
    log_event: LogEvent,
) -> APIRouter:
    """Build transparent Robot API v1 routes without broadcasts."""
    router = APIRouter(prefix=protocol.API_PREFIX, tags=["Robot Relay"])

    async def forward(
        request: Request,
        path: str,
        audit_action: str | None = None,
    ) -> Response:
        response = await get_relay().forward(
            request,
            path,
            priority_stop=path == protocol.PATH_STOP,
        )
        if audit_action is not None:
            try:
                log_event(
                    event_type="robot_command",
                    description=(
                        f"Relayed {audit_action} to Pi Robot API; "
                        f"HTTP {response.status_code}."
                    ),
                )
            except Exception:
                # Audit persistence must not delay or replace the Pi response,
                # especially for motion and STOP.
                logger.exception("Could not write robot relay audit event")
        return response

    @router.post("/session")
    async def acquire_session(request: Request):
        return await forward(request, protocol.PATH_SESSION)

    @router.delete("/session")
    async def release_session(request: Request):
        return await forward(request, protocol.PATH_SESSION)

    @router.post("/motion")
    async def motion(request: Request):
        return await forward(request, protocol.PATH_MOTION, "motion")

    @router.post("/stop")
    async def stop(request: Request):
        return await forward(request, protocol.PATH_STOP, "stop")

    @router.post("/movement-style")
    async def movement_style(request: Request):
        return await forward(request, protocol.PATH_MOVEMENT_STYLE)

    @router.get("/actions")
    async def actions(request: Request):
        return await forward(request, protocol.PATH_ACTIONS)

    @router.post("/actions/{action_id}")
    async def run_action(action_id: int, request: Request):
        path = protocol.PATH_ACTION.format(action_id=action_id)
        return await forward(request, path, f"action:{action_id}")

    @router.get("/telemetry")
    async def telemetry(request: Request):
        return await forward(request, protocol.PATH_TELEMETRY)

    @router.get("/status")
    async def status(request: Request):
        return await forward(request, protocol.PATH_STATUS)

    return router
