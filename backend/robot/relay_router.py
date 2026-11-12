"""Quest-facing Robot API v1 routes backed by the shared CommandGateway."""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.robot import protocol
from backend.robot.interface import CommandResult, Direction, Intensity, MotionCommand
from backend.gateway.gateway import CommandGateway, GatewayResult

logger = logging.getLogger(__name__)
LogEvent = Callable[..., Any]
QUEST_API_KEY_ENV = "QUEST_API_KEY"
REPEAT_INTERVAL_S = 0.2
CONTINUOUS_WINDOW_ENV = "ROBOT_CONTINUOUS_MOTION_WINDOW_S"
CONTINUOUS_WINDOW_DEFAULT_S = 1.0
CONTINUOUS_WINDOW_MIN_S, CONTINUOUS_WINDOW_MAX_S = 0.2, 2.0
PRIORITY_PATHS = frozenset({protocol.PATH_STOP, protocol.PATH_EMERGENCY_STOP})


class _RelayMotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    forward: float = Field(0.0, ge=-1.0, le=1.0, allow_inf_nan=False)
    strafe: float = Field(0.0, ge=-1.0, le=1.0, allow_inf_nan=False)
    turn: float = Field(0.0, ge=-1.0, le=1.0, allow_inf_nan=False)


class _RelayDiscreteMotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Direction
    intensity: Intensity | None = None


def continuous_window_s() -> float:
    try:
        value = float(os.environ.get(CONTINUOUS_WINDOW_ENV, CONTINUOUS_WINDOW_DEFAULT_S))
    except (TypeError, ValueError):
        return CONTINUOUS_WINDOW_DEFAULT_S
    if not value == value or value in (float("inf"), float("-inf")):
        return CONTINUOUS_WINDOW_DEFAULT_S
    return min(max(value, CONTINUOUS_WINDOW_MIN_S), CONTINUOUS_WINDOW_MAX_S)


class QuestRelayAuth:
    """Fail-closed authentication for Quest requests entering the laptop."""
    def __init__(self, api_key: str | None, *, allow_anonymous: bool = False) -> None:
        self._key = api_key.strip().encode() if api_key and api_key.strip() else None
        self._allow_anonymous = allow_anonymous

    @classmethod
    def from_env(cls) -> "QuestRelayAuth":
        anon = os.environ.get("QUEST_API_KEY_ALLOW_ANONYMOUS", "").lower() in ("1", "true", "yes")
        return cls(os.environ.get(QUEST_API_KEY_ENV), allow_anonymous=anon)

    def __call__(self, request: Request) -> None:
        if self._allow_anonymous or (request.method == "POST" and request.url.path in PRIORITY_PATHS):
            return
        if self._key is None:
            raise HTTPException(status_code=503, detail="robot relay authentication is not configured")
        candidate = request.headers.get(protocol.AUTH_HEADER)
        if not candidate or not hmac.compare_digest(candidate.encode(), self._key):
            raise HTTPException(status_code=401, detail="invalid or missing robot key",
                                headers={"WWW-Authenticate": "RobotKey"})


def create_robot_relay_router(
    get_gateway: Callable[[], CommandGateway], log_event: LogEvent,
    auth: QuestRelayAuth | None = None,
) -> APIRouter:
    """Keep Quest API paths and payloads while routing robot work through the shared gateway."""
    quest_auth = auth or QuestRelayAuth.from_env()
    router = APIRouter(prefix=protocol.API_PREFIX, tags=["Robot Relay"], dependencies=[Depends(quest_auth)])
    session_id: str | None = None
    last_seq = -1
    session_expires_at = 0.0
    motion_active = False
    repeat_task: asyncio.Task | None = None
    last_motion_time = 0.0

    def result_response(result: GatewayResult | CommandResult, *, status_override: int | None = None,
                        command: str = "robot") -> JSONResponse:
        if isinstance(result, GatewayResult):
            status_text = result.status.lower() if result.status in {"ACCEPTED", "STOP_DISPATCHED", "REJECTED", "FAILED"} else "rejected"
            body = {"status": status_text, "command": command,
                    "message": result.message, "command_id": result.command_id, "source": result.source}
            if result.details and "intensity" in result.details:
                body["intensity"] = result.details["intensity"]
            http_status = status_override or (result.details.get("http_status") if result.details and result.details.get("http_status") else None) or (200 if result.status in {"ACCEPTED", "STOP_DISPATCHED"} else
                409 if result.status in {"BUSY", "CANCELLED", "ESTOP_ACTIVE"} else
                503 if result.status == "OFFLINE" else 403 if result.status == "MISSION_REQUIRED" else 422)
            if http_status >= 400:
                body["detail"] = f"{result.status}: {result.message}"
        else:
            body = result.to_dict()
            http_status = status_override or (200 if result.ok else 422)
            if http_status >= 400:
                body["detail"] = f"{result.status.value.upper()}: {result.message}"
        return JSONResponse(body, status_code=http_status)

    async def gateway_call(request: Request, method: str, *args: Any, **kwargs: Any) -> Any:
        gateway = get_gateway()
        command_id = protocol.sanitize_command_id(request.headers.get(protocol.COMMAND_ID_HEADER))
        return await gateway.with_command_id(command_id, lambda: getattr(gateway, method)(*args, **kwargs))

    async def cancel_repeat() -> None:
        """Cancel and join the continuous hand-repeat task before stop-class commands."""
        task = repeat_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def repeat_loop(repeat_call: Callable[[], Any], stop_call: Callable[[], Any]) -> None:
        """Repeat hand motion through the gateway and stop if the repeat fails or expires."""
        nonlocal motion_active
        me = asyncio.current_task()
        deadline = last_motion_time + continuous_window_s()
        should_stop = True
        try:
            while motion_active and time.monotonic() < deadline:
                await asyncio.sleep(min(REPEAT_INTERVAL_S, max(0, deadline - time.monotonic())))
                if not motion_active or time.monotonic() >= deadline:
                    break
                result = await repeat_call()
                if result.status not in {"ACCEPTED", "STOP_DISPATCHED"}:
                    logger.warning("Continuous-motion repeat failed with %s; stopping.", result.status)
                    break
        except asyncio.CancelledError:
            should_stop = False
            raise
        except Exception as exc:
            logger.warning("Continuous-motion repeat failed (%s); stopping.", type(exc).__name__)
        finally:
            if should_stop and motion_active and repeat_task is me:
                motion_active = False
                try:
                    await stop_call()
                except Exception:
                    logger.warning("Continuous-motion stop could not be delivered through the gateway.")

    def audit(description: str, request: Request, response: Response, started: float) -> None:
        try:
            log_event(event_type="robot_command", description=description,
                      source=protocol.sanitize_command_source(request.headers.get(protocol.SOURCE_HEADER)),
                      command_id=protocol.sanitize_command_id(request.headers.get(protocol.COMMAND_ID_HEADER)),
                      result_code=response.status_code, latency_ms=(time.perf_counter() - started) * 1000)
        except Exception:
            logger.exception("Could not write robot relay audit event")

    async def checked(request: Request, *, priority: bool = False) -> dict[str, Any] | JSONResponse:
        nonlocal last_seq, session_id, session_expires_at
        if priority:
            return {}
        supplied_session = request.headers.get(protocol.SESSION_HEADER)
        try:
            seq = int(request.headers.get(protocol.SEQ_HEADER, ""))
        except ValueError:
            seq = -1
        if (session_id is None or time.monotonic() >= session_expires_at or
                supplied_session != session_id or seq <= last_seq):
            return JSONResponse({"detail": "Invalid or stale control session/sequence."}, status_code=409)
        last_seq = seq
        session_expires_at = time.monotonic() + 10.0
        return {}

    @router.post("/session")
    async def acquire_session(request: Request):
        nonlocal session_id, last_seq, session_expires_at
        session_id = uuid.uuid4().hex
        last_seq = -1
        session_expires_at = time.monotonic() + 10.0
        return JSONResponse({"session_id": session_id, "ttl_s": 10.0}, status_code=201)

    @router.delete("/session")
    async def release_session(request: Request):
        nonlocal session_id, motion_active, repeat_task, session_expires_at
        invalid = await checked(request)
        if isinstance(invalid, Response): return invalid
        await cancel_repeat()
        motion_active = False
        result = await gateway_call(request, "stop", source="hand")
        session_id = None
        session_expires_at = 0.0
        return result_response(result)

    async def run_motion(request: Request, *, discrete: bool = False) -> Response:
        nonlocal motion_active, repeat_task, last_motion_time
        invalid = await checked(request)
        if isinstance(invalid, Response): return invalid
        started = time.perf_counter()
        try:
            data = await request.json()
            if discrete:
                payload = _RelayDiscreteMotionRequest.model_validate(data)
                direction, intensity = payload.direction, payload.intensity
                await cancel_repeat()
                result = await gateway_call(request, "move_discrete", direction, intensity, source="hand")
                moving = True
                repeat_call = lambda: gateway_call(request, "move_discrete", direction, intensity, source="hand")
            else:
                payload = _RelayMotionRequest.model_validate(data)
                command = MotionCommand(payload.forward, payload.strafe, payload.turn)
                moving = not command.is_zero
                if moving:
                    await cancel_repeat()
                result = await gateway_call(request, "motion", command, source="hand")
                repeat_call = lambda: gateway_call(request, "motion", command, source="hand")
        except (ValidationError, ValueError, TypeError, AttributeError) as exc:
            message = str(exc)
            return JSONResponse({"status": "rejected", "command": "motion", "message": message,
                                 "detail": f"INVALID: {message}"}, status_code=422)
        response = result_response(result, command="motion/discrete" if discrete else "motion")
        if not response.status_code // 100 == 2:
            if motion_active:
                await gateway_call(request, "stop", source="hand")
                motion_active = False
            audit(f"Quest motion failed; HTTP {response.status_code}.", request, response, started)
        elif moving and not motion_active:
            motion_active = True
            audit("Quest motion started.", request, response, started)
        elif not moving and motion_active:
            motion_active = False
            audit("Quest motion stopped.", request, response, started)
        if moving and response.status_code // 100 == 2:
            last_motion_time = time.monotonic()
            if os.environ.get("ROBOT_CONTINUOUS_MOTION", "true").lower() in ("1", "true", "yes"):
                stop_call = lambda: gateway_call(request, "stop", source="hand")
                repeat_task = asyncio.create_task(repeat_loop(repeat_call, stop_call))
        elif not moving:
            await cancel_repeat()
        return response

    @router.post("/motion")
    async def motion(request: Request): return await run_motion(request)

    @router.post("/motion/discrete")
    async def motion_discrete(request: Request): return await run_motion(request, discrete=True)

    @router.post("/stop")
    async def stop(request: Request):
        nonlocal motion_active
        await cancel_repeat()
        motion_active = False
        started = time.perf_counter(); result = await gateway_call(request, "stop", source="hand")
        response = result_response(result, command="stop"); audit(f"Quest stop; HTTP {response.status_code}.", request, response, started); return response

    @router.post("/emergency-stop")
    async def emergency_stop(request: Request):
        nonlocal motion_active
        await cancel_repeat()
        motion_active = False
        started = time.perf_counter(); result = await gateway_call(request, "emergency_stop", source="emergency")
        response = result_response(result, command="emergency_stop"); audit(f"Quest emergency_stop; HTTP {response.status_code}.", request, response, started); return response

    @router.post("/emergency-stop/reset")
    async def emergency_reset(request: Request):
        nonlocal motion_active
        started = time.perf_counter()
        invalid = await checked(request)
        if isinstance(invalid, Response): return invalid
        await cancel_repeat()
        motion_active = False
        response = result_response(await gateway_call(request, "reset_emergency_stop", source="hand"), command="emergency_stop_reset")
        audit(f"Relayed emergency_stop_reset; HTTP {response.status_code}.", request, response, started)
        return response

    @router.post("/posture")
    async def posture(request: Request):
        invalid = await checked(request)
        if isinstance(invalid, Response): return invalid
        return result_response(await gateway_call(request, "set_posture", await request.json(), source="hand"), command="posture")

    @router.post("/reset")
    async def reset(request: Request):
        nonlocal motion_active
        started = time.perf_counter()
        invalid = await checked(request)
        if isinstance(invalid, Response): return invalid
        await cancel_repeat()
        motion_active = False
        response = result_response(await gateway_call(request, "reset", source="hand"), command="reset")
        audit(f"Relayed reset; HTTP {response.status_code}.", request, response, started)
        return response

    @router.post("/movement-style")
    async def movement_style(request: Request):
        invalid = await checked(request)
        if isinstance(invalid, Response): return invalid
        data = await request.json()
        return result_response(await gateway_call(request, "set_style", data.get("intensity"), data.get("gait"), source="hand"), command="movement_style")

    @router.get("/actions")
    async def actions(request: Request):
        items = await get_gateway().list_actions(source="hand")
        return JSONResponse({"actions": [{"id": x.id, "name": x.name} for x in items]})

    @router.post("/actions/{action_id}")
    async def run_action(action_id: int, request: Request):
        started = time.perf_counter()
        invalid = await checked(request)
        if isinstance(invalid, Response): return invalid
        response = result_response(await gateway_call(request, "run_action", action_id, source="hand"), command=f"action:{action_id}")
        audit(f"Relayed action:{action_id}; HTTP {response.status_code}.", request, response, started)
        return response

    @router.get("/telemetry")
    async def telemetry(request: Request):
        state = await get_gateway().status(source="hand")
        return JSONResponse(state.get("telemetry", {}))

    @router.get("/status")
    async def status_route(request: Request):
        state = await get_gateway().status(source="hand")
        control = state.get("control_state", {})
        robot = get_gateway().robot
        remote_url = getattr(getattr(robot, "config", None), "remote_url", None) or os.environ.get("ROBOT_REMOTE_URL")
        return JSONResponse({"robot_mode": getattr(robot.robot_mode, "value", "simulation"),
                             "connected": state.get("online", False), "control_session_active": state.get("online", False),
                             "last_command_age_ms": 0, "watchdog": {"armed": state.get("online", False), "timeout_ms": 0},
                             "intensity": control.get("intensity", "medium"), "gait": control.get("gait", "trot"),
                             "robot_remote_url": remote_url})

    return router
