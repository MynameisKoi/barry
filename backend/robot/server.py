"""Authenticated, safety-oriented Pi-side Robot API.

Transport safety lives here; hardware-specific validation remains in each
``RobotInterface`` implementation. Successful responses mean dispatched, not
physically verified.
"""

from __future__ import annotations

import asyncio
import hmac
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from backend.robot.config import APPROVED_ACTIONS, RobotConfig
from backend.robot.interface import (
    CommandResult, CommandStatus, Direction, Gait, HeightPreset, Intensity,
    MotionCommand, MovementStyle, Posture, RobotInterface, RobotMode,
)
from backend.robot.protocol import (
    AUTH_HEADER, PATH_ACTION, PATH_ACTIONS, PATH_EMERGENCY_RESET,
    PATH_EMERGENCY_STOP, PATH_HEALTH, PATH_MOTION, PATH_MOTION_DISCRETE,
    PATH_MOVEMENT_STYLE, PATH_POSTURE, PATH_RESET, PATH_SESSION, PATH_STATUS,
    PATH_STOP, PATH_TELEMETRY, SENT_AT_HEADER, SEQ_HEADER, SESSION_HEADER,
)


class MotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    forward: float = Field(0.0, ge=-1.0, le=1.0, allow_inf_nan=False)
    strafe: float = Field(0.0, ge=-1.0, le=1.0, allow_inf_nan=False)
    turn: float = Field(0.0, ge=-1.0, le=1.0, allow_inf_nan=False)


class DiscreteMotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Direction
    intensity: Optional[Intensity] = None


class MovementStyleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intensity: Intensity
    gait: Gait


class PostureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    height: HeightPreset
    roll: float = Field(0.0, allow_inf_nan=False)
    pitch: float = Field(0.0, allow_inf_nan=False)
    yaw: float = Field(0.0, allow_inf_nan=False)


@dataclass
class _Session:
    session_id: str
    expires_at: float
    last_seq: int = -1


class _State:
    """Mutable safety state belonging to exactly one app instance."""

    def __init__(self, robot: RobotInterface, config: RobotConfig) -> None:
        self.robot, self.config = robot, config
        self.dispatch_lock = asyncio.Lock()
        self.state_lock = asyncio.Lock()
        self.session: Optional[_Session] = None
        self.generation = 0
        self.last_motion_at: Optional[float] = None
        self.last_command_at: Optional[float] = None
        self.watchdog_armed = False
        self.watchdog_task: Optional[asyncio.Task[None]] = None

    async def stop(self, invalidate: bool = True) -> CommandResult:
        """Bypass the dispatch lock so stop is never queued behind motion."""
        if invalidate:
            async with self.state_lock:
                self.generation += 1
                self.watchdog_armed = False
                self.last_command_at = time.monotonic()
        return await run_in_threadpool(self.robot.stop)

    async def expire_session(self) -> None:
        expired = False
        async with self.state_lock:
            if self.session and self.session.expires_at <= time.monotonic():
                self.session = None
                self.generation += 1
                self.watchdog_armed = False
                expired = True
        if expired:
            await self.stop(invalidate=False)

    async def watchdog(self) -> None:
        interval = min(max(self.config.watchdog_ms / 4000, 0.005), 0.1)
        while True:
            await asyncio.sleep(interval)
            await self.expire_session()
            fire = False
            async with self.state_lock:
                if (self.watchdog_armed and self.last_motion_at is not None and
                        time.monotonic() - self.last_motion_at >= self.config.watchdog_ms / 1000):
                    self.generation += 1
                    self.watchdog_armed = False
                    fire = True
            if fire:
                await self.stop(invalidate=False)


def create_app(robot: RobotInterface, config: RobotConfig) -> FastAPI:
    """Create an isolated API using dependency-injected robot and settings."""
    if config.mode == RobotMode.DOGZILLA and not config.api_key:
        raise RuntimeError("ROBOT_API_KEY is required in dogzilla mode")
    state = _State(robot, config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await state.stop(invalidate=False)
        state.watchdog_task = asyncio.create_task(state.watchdog())
        try:
            yield
        finally:
            state.watchdog_task.cancel()
            try:
                await state.watchdog_task
            except asyncio.CancelledError:
                pass
            await state.stop(invalidate=False)

    api = FastAPI(title="Barry Pi Robot API", version="1.0", lifespan=lifespan)
    api.state.robot_server = state

    async def authenticate(value: Optional[str] = Header(None, alias=AUTH_HEADER)) -> None:
        if not config.api_key or not hmac.compare_digest(value or "", config.api_key):
            raise HTTPException(401, "Invalid robot API key")

    async def command_context(
        session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
        seq: Optional[int] = Header(None, alias=SEQ_HEADER),
        sent_at: Optional[float] = Header(None, alias=SENT_AT_HEADER),
        _: None = Depends(authenticate),
    ) -> int:
        await state.expire_session()
        async with state.state_lock:
            session = state.session
            if not session or not session_id or not hmac.compare_digest(session.session_id, session_id):
                raise HTTPException(409, "A valid control session is required")
            if seq is None or seq <= session.last_seq:
                raise HTTPException(409, "Stale command sequence")
            if config.max_command_age_ms:
                if sent_at is None:
                    raise HTTPException(409, "Command timestamp is required")
                age_ms = time.time() * 1000 - sent_at
                if age_ms < 0 or age_ms > config.max_command_age_ms:
                    raise HTTPException(409, "Stale command timestamp")
            session.last_seq = seq
            session.expires_at = time.monotonic() + config.session_ttl_s
            return state.generation

    async def dispatch(call: Callable[[], Any], generation: int, motion: bool = False) -> Any:
        if not robot.connected:
            raise HTTPException(503, "Robot is disconnected")
        if robot.get_control_state().emergency_stopped:
            raise HTTPException(423, "Emergency stop is latched")
        async with state.dispatch_lock:
            async with state.state_lock:
                if generation != state.generation:
                    raise HTTPException(409, "Command invalidated by stop")
            result = await run_in_threadpool(call)
            invalidated = False
            async with state.state_lock:
                invalidated = generation != state.generation
                if not invalidated:
                    state.last_command_at = time.monotonic()
                    if motion and isinstance(result, CommandResult) and result.ok:
                        state.last_motion_at = state.last_command_at
                        state.watchdog_armed = robot.get_control_state().moving
            if invalidated:
                # A synchronous SDK call cannot be cancelled after it starts.
                # Reassert stop after it returns so it cannot outlive the
                # priority stop that bumped the generation.
                if motion:
                    await state.stop(invalidate=False)
                raise HTTPException(409, "Command invalidated by stop")
        if isinstance(result, CommandResult):
            if result.status == CommandStatus.REJECTED:
                raise HTTPException(422, result.message or "Command rejected")
            if result.status == CommandStatus.FAILED:
                raise HTTPException(503, result.message or "Command failed")
            return result.to_dict()
        return result

    @api.get(PATH_HEALTH)
    async def health() -> dict[str, Any]:
        return {"status": "ok", "connected": robot.connected}

    @api.post(PATH_SESSION, dependencies=[Depends(authenticate)])
    async def acquire_session(
        session_id: Optional[str] = Header(None, alias=SESSION_HEADER),
    ) -> dict[str, Any]:
        await state.expire_session()
        async with state.state_lock:
            if state.session:
                if not session_id or not hmac.compare_digest(state.session.session_id, session_id):
                    raise HTTPException(409, "Control session already active")
                state.session.expires_at = time.monotonic() + config.session_ttl_s
                return {"session_id": state.session.session_id, "ttl_s": config.session_ttl_s}
            new_id = str(uuid.uuid4())
            state.session = _Session(new_id, time.monotonic() + config.session_ttl_s)
            return {"session_id": new_id, "ttl_s": config.session_ttl_s}

    @api.delete(PATH_SESSION, dependencies=[Depends(authenticate)])
    async def release_session(session_id: Optional[str] = Header(None, alias=SESSION_HEADER)) -> dict[str, Any]:
        async with state.state_lock:
            if not state.session or not session_id or not hmac.compare_digest(state.session.session_id, session_id):
                raise HTTPException(409, "A valid control session is required")
            state.session = None
        return (await state.stop()).to_dict()

    @api.post(PATH_MOTION)
    async def motion(body: MotionRequest, generation: int = Depends(command_context)) -> dict[str, Any]:
        return await dispatch(lambda: robot.move_analog(MotionCommand(body.forward, body.strafe, body.turn)), generation, True)

    @api.post(PATH_MOTION_DISCRETE)
    async def discrete(body: DiscreteMotionRequest, generation: int = Depends(command_context)) -> dict[str, Any]:
        return await dispatch(lambda: robot.move_discrete(body.direction, body.intensity), generation, True)

    @api.post(PATH_STOP, dependencies=[Depends(authenticate)])
    async def stop() -> dict[str, Any]:
        return (await state.stop()).to_dict()

    @api.post(PATH_EMERGENCY_STOP, dependencies=[Depends(authenticate)])
    async def emergency_stop() -> dict[str, Any]:
        async with state.state_lock:
            state.generation += 1
            state.watchdog_armed = False
        return await run_in_threadpool(robot.emergency_stop)

    @api.post(PATH_EMERGENCY_RESET)
    async def emergency_reset(generation: int = Depends(command_context)) -> dict[str, Any]:
        async with state.dispatch_lock:
            async with state.state_lock:
                if generation != state.generation:
                    raise HTTPException(409, "Command invalidated by stop")
            return await run_in_threadpool(robot.reset_emergency_stop)

    @api.post(PATH_MOVEMENT_STYLE)
    async def movement_style(body: MovementStyleRequest, generation: int = Depends(command_context)) -> dict[str, Any]:
        return await dispatch(lambda: robot.set_movement_style(MovementStyle(body.intensity, body.gait)), generation)

    @api.post(PATH_POSTURE)
    async def posture(body: PostureRequest, generation: int = Depends(command_context)) -> dict[str, Any]:
        return await dispatch(lambda: robot.set_posture(Posture(body.height, body.roll, body.pitch, body.yaw)), generation)

    @api.get(PATH_ACTIONS, dependencies=[Depends(authenticate)])
    async def actions() -> dict[str, Any]:
        values = await run_in_threadpool(robot.list_actions)
        return {"actions": [{"id": value.id, "name": value.name} for value in values]}

    @api.post(PATH_ACTION)
    async def action(action_id: int, generation: int = Depends(command_context)) -> dict[str, Any]:
        if action_id not in APPROVED_ACTIONS:
            raise HTTPException(422, "Unknown action ID")
        return await dispatch(lambda: robot.perform_action(action_id), generation)

    @api.post(PATH_RESET)
    async def reset(generation: int = Depends(command_context)) -> dict[str, Any]:
        return await dispatch(robot.reset, generation)

    @api.get(PATH_TELEMETRY, dependencies=[Depends(authenticate)])
    async def telemetry() -> dict[str, Any]:
        if not robot.connected:
            raise HTTPException(503, "Robot is disconnected")
        return (await run_in_threadpool(robot.read_telemetry)).to_dict()

    @api.get(PATH_STATUS, dependencies=[Depends(authenticate)])
    async def status() -> dict[str, Any]:
        await state.expire_session()
        control = robot.get_control_state()
        async with state.state_lock:
            age = None if state.last_command_at is None else int((time.monotonic() - state.last_command_at) * 1000)
            active, armed = state.session is not None, state.watchdog_armed
        return {
            "robot_mode": robot.robot_mode.value, "connected": robot.connected,
            "control_session_active": active, "last_command_age_ms": age,
            "watchdog": {"armed": armed, "timeout_ms": config.watchdog_ms},
            "intensity": control.intensity.value, "gait": control.gait.value,
        }

    return api


def _build_default_app() -> FastAPI:
    """Construct the uvicorn app; factory import stays lazy for Pi-only code."""
    config = RobotConfig.from_env()
    try:
        from backend.robot.factory import create_robot  # type: ignore[attr-defined]
    except ImportError:
        create_robot = None
    if create_robot is not None:
        robot = create_robot(config)
    elif config.mode == RobotMode.SIMULATOR:
        from backend.robot.simulator import RobotSimulator
        try:
            robot = RobotSimulator()
        except TypeError:
            # TODO: remove once the v1 simulator/factory integration lands.
            # The legacy simulator on the contract branch is abstract under
            # the new interface, so keep imports and health checks safe while
            # refusing all hardware operations.
            class FactoryPendingRobot:
                robot_mode = RobotMode.SIMULATOR
                connected = False

                def stop(self) -> CommandResult:
                    return CommandResult(CommandStatus.FAILED, "stop", "Robot factory is unavailable")

                def get_control_state(self):
                    from backend.robot.interface import ControlState
                    return ControlState()

            robot = FactoryPendingRobot()
    else:
        raise RuntimeError("Robot factory is unavailable for non-simulator mode")
    return create_app(robot, config)


app = _build_default_app()
