"""Quest-facing ``/api/v1`` robot routes relayed by the laptop to the Pi."""

from __future__ import annotations

import asyncio
import logging
import hmac
import os
import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from backend.robot import protocol
from backend.robot.relay import RobotApiRelay

logger = logging.getLogger(__name__)
LogEvent = Callable[..., Any]
QUEST_API_KEY_ENV = "QUEST_API_KEY"
# Safety paths that must get through while a motion request is in flight: the dedicated
# priority client, never queued behind motion.
PRIORITY_PATHS = frozenset({protocol.PATH_STOP, protocol.PATH_EMERGENCY_STOP})


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
    quest_auth = auth or QuestRelayAuth.from_env()

    def relay_auth(request: Request) -> None:
        # Stop and e-stop are never gated by auth (architecture principle 5, Tom 2026-11-10):
        # a headset with a missing or wrong key, or a laptop with no QUEST_API_KEY, can still
        # halt the robot. They can only make it safer. The laptop still sends its own
        # ROBOT_API_KEY to the Pi. Every other route stays keyed, including future ones,
        # because this is an explicit allowlist of two paths.
        if request.method == "POST" and request.url.path in PRIORITY_PATHS:
            return
        quest_auth(request)

    router = APIRouter(
        prefix=protocol.API_PREFIX,
        tags=["Robot Relay"],
        dependencies=[Depends(relay_auth)],
    )
    motion_active = False
    repeat_task: asyncio.Task | None = None
    last_motion_time = 0.0

    async def repeat_loop(body_bytes: bytes, headers: dict):
        nonlocal motion_active
        try:
            while motion_active and (time.monotonic() - last_motion_time) < 2.0:
                await asyncio.sleep(0.2)
                if not motion_active:
                    break
                await get_relay()._client.request(
                    "POST",
                    protocol.PATH_MOTION,
                    content=body_bytes,
                    headers=headers,
                    timeout=1.5,
                )
        except Exception:
            pass
        finally:
            if motion_active and (time.monotonic() - last_motion_time) >= 2.0:
                motion_active = False
                try:
                    await get_relay()._stop_client.request(
                        "POST", protocol.PATH_STOP, headers=headers, timeout=1.5
                    )
                except Exception:
                    pass

    def audit(description: str, request: Request, response: Response, started: float) -> None:
        try:
            log_event(
                event_type="robot_command", description=description,
                source=protocol.sanitize_command_source(request.headers.get(protocol.SOURCE_HEADER)),
                command_id=protocol.sanitize_command_id(request.headers.get(protocol.COMMAND_ID_HEADER)),
                result_code=response.status_code,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception:
            # Audit persistence must never delay or replace the Pi response.
            logger.exception("Could not write robot relay audit event")

    async def forward(
        request: Request,
        path: str,
        audit_action: str | None = None,
    ) -> Response:
        started = time.perf_counter()
        response = await get_relay().forward(
            request,
            path,
            priority_stop=path in PRIORITY_PATHS,
        )
        if audit_action is not None and audit_action != "motion":
            audit(f"Relayed {audit_action} to Pi Robot API; HTTP {response.status_code}.", request, response, started)
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

    async def relay_motion(request: Request, path: str, moving: bool | None) -> Response:
        """Relay motion and audit transitions and failures only."""
        nonlocal motion_active, repeat_task, last_motion_time
        started = time.perf_counter()
        response = await forward(request, path)
        if not 200 <= response.status_code < 300:
            audit(f"Relayed motion to Pi Robot API; HTTP {response.status_code}.", request, response, started)
        elif moving is not None:
            if moving and not motion_active:
                motion_active = True
                audit(f"Relayed motion started to Pi Robot API; HTTP {response.status_code}.", request, response, started)
            elif not moving and motion_active:
                motion_active = False
                audit(f"Relayed motion stopped to Pi Robot API; HTTP {response.status_code}.", request, response, started)

        if moving and 200 <= response.status_code < 300:
            last_motion_time = time.monotonic()
            if os.environ.get("ROBOT_CONTINUOUS_MOTION", "true").lower() in ("1", "true", "yes"):
                if repeat_task and not repeat_task.done():
                    repeat_task.cancel()
                headers = get_relay()._request_headers(request)
                repeat_task = asyncio.create_task(repeat_loop(await request.body(), headers))
        elif not moving and repeat_task and not repeat_task.done():
            repeat_task.cancel()

        return response

    @router.post("/motion")
    async def motion(request: Request):
        try:
            command = await request.json()
            moving = any(
                isinstance(command.get(axis), (int, float)) and command.get(axis) != 0
                for axis in ("forward", "strafe", "turn")
            )
        except (ValueError, AttributeError):
            moving = None
        return await relay_motion(request, protocol.PATH_MOTION, moving)

    @router.post("/motion/discrete")
    async def motion_discrete(request: Request):
        # Every discrete direction is a movement (there is no "stop" direction), so a request
        # that names one is "moving"; a body we can't read is relayed unaudited unless it fails.
        try:
            command = await request.json()
            moving = True if isinstance(command, dict) and command.get("direction") else None
        except ValueError:
            moving = None
        return await relay_motion(request, protocol.PATH_MOTION_DISCRETE, moving)

    @router.post("/stop")
    async def stop(request: Request):
        nonlocal motion_active, repeat_task
        if repeat_task and not repeat_task.done():
            repeat_task.cancel()
        response = await forward(request, protocol.PATH_STOP, "stop")
        if 200 <= response.status_code < 300:
            motion_active = False
        return response

    @router.post("/emergency-stop")
    async def emergency_stop(request: Request):
        nonlocal motion_active
        # Priority lane (PRIORITY_PATHS) and audited on every request, like /stop.
        response = await forward(request, protocol.PATH_EMERGENCY_STOP, "emergency_stop")
        if 200 <= response.status_code < 300:
            motion_active = False
        return response

    @router.post("/emergency-stop/reset")
    async def emergency_stop_reset(request: Request):
        return await forward(request, protocol.PATH_EMERGENCY_RESET, "emergency_stop_reset")

    @router.post("/posture")
    async def posture(request: Request):
        return await forward(request, protocol.PATH_POSTURE)

    @router.post("/reset")
    async def reset(request: Request):
        nonlocal motion_active
        response = await forward(request, protocol.PATH_RESET, "reset")
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
