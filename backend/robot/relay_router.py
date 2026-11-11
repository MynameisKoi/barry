"""Quest-facing ``/api/v1`` robot routes relayed by the laptop to the Pi."""

from __future__ import annotations

import logging
import hmac
import os
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from backend.robot import protocol
from backend.robot.relay import RobotApiRelay

logger = logging.getLogger(__name__)
LogEvent = Callable[..., Any]
QUEST_API_KEY_ENV = "QUEST_API_KEY"


class QuestRelayAuth:
    """Fail-closed authentication for Quest requests entering the laptop relay."""

    def __init__(self, api_key: str | None, *, allow_anonymous: bool = False) -> None:
        self._key = api_key.strip().encode("utf-8") if api_key and api_key.strip() else None
        self._allow_anonymous = allow_anonymous

    @classmethod
    def from_env(cls) -> "QuestRelayAuth":
        allow_anon = os.environ.get("QUEST_API_KEY_ALLOW_ANONYMOUS", "").lower() in ("1", "true", "yes")
        return cls(os.environ.get(QUEST_API_KEY_ENV), allow_anonymous=allow_anon)

    def __call__(self, request: Request) -> None:
        if self._allow_anonymous:
            return
        if self._key is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="robot relay authentication is not configured",
            )
        candidate = request.headers.get(protocol.AUTH_HEADER)
        if not candidate or not hmac.compare_digest(candidate.encode("utf-8"), self._key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing robot key",
                headers={"WWW-Authenticate": "RobotKey"},
            )


def create_robot_relay_router(
    get_relay: Callable[[], RobotApiRelay],
    log_event: LogEvent,
    auth: QuestRelayAuth | None = None,
) -> APIRouter:
    """Build transparent Robot API v1 routes without broadcasts."""
    router = APIRouter(
        prefix=protocol.API_PREFIX,
        tags=["Robot Relay"],
        dependencies=[Depends(auth or QuestRelayAuth.from_env())],
    )
    motion_active = False

    def audit(description: str) -> None:
        try:
            log_event(event_type="robot_command", description=description)
        except Exception:
            # Audit persistence must never delay or replace the Pi response.
            logger.exception("Could not write robot relay audit event")

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
        if audit_action is not None and audit_action != "motion":
            audit(
                f"Relayed {audit_action} to Pi Robot API; HTTP {response.status_code}."
            )
        return response

    @router.post("/session")
    async def acquire_session(request: Request):
        return await forward(request, protocol.PATH_SESSION)

    @router.delete("/session")
    async def release_session(request: Request):
        nonlocal motion_active
        response = await forward(request, protocol.PATH_SESSION)
        if 200 <= response.status_code < 300:
            motion_active = False
        return response

    @router.post("/motion")
    async def motion(request: Request):
        nonlocal motion_active
        try:
            command = await request.json()
            moving = any(
                isinstance(command.get(axis), (int, float)) and command.get(axis) != 0
                for axis in ("forward", "strafe", "turn")
            )
        except (ValueError, AttributeError):
            moving = None

        response = await forward(request, protocol.PATH_MOTION)
        if not 200 <= response.status_code < 300:
            audit(f"Relayed motion to Pi Robot API; HTTP {response.status_code}.")
        elif moving is not None:
            if moving and not motion_active:
                motion_active = True
                audit(f"Relayed motion started to Pi Robot API; HTTP {response.status_code}.")
            elif not moving and motion_active:
                motion_active = False
                audit(f"Relayed motion stopped to Pi Robot API; HTTP {response.status_code}.")
        return response

    @router.post("/stop")
    async def stop(request: Request):
        nonlocal motion_active
        response = await forward(request, protocol.PATH_STOP, "stop")
        if 200 <= response.status_code < 300:
            motion_active = False
        return response

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
