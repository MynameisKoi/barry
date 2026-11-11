"""Async command gateway over the synchronous RobotInterface."""

from __future__ import annotations

import asyncio
import math
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from backend.robot.factory import create_robot
from backend.robot.interface import Direction, MotionCommand, RobotInterface
from backend.services.mission import mission_service

SOURCES = {"hand", "voice", "llm", "emergency"}


@dataclass
class GatewayResult:
    status: str
    message: str
    command_id: str
    source: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "message": self.message,
                "command_id": self.command_id, "source": self.source,
                **({"details": self.details} if self.details else {})}


class CommandGateway:
    """Owns robot commands, the Pi session (through its client), and arbitration."""

    def __init__(self, robot: RobotInterface | None = None, *, mission_check: Callable[[], bool] | None = None,
                 max_speed: float | None = None, max_bounded_s: float | None = None,
                 turn_deg_per_s: float | None = None) -> None:
        self.robot = robot or create_robot()
        self.mission_check = mission_check or (lambda: bool(mission_service.get_status().get("active")))
        self.max_speed = _env_float("GATEWAY_MAX_SPEED", 0.6) if max_speed is None else max_speed
        self.max_bounded_s = _env_float("GATEWAY_MAX_BOUNDED_S", 5.0) if max_bounded_s is None else max_bounded_s
        self.turn_deg_per_s = _env_float("ROBOT_TURN_DEG_PER_S", 90.0) if turn_deg_per_s is None else turn_deg_per_s
        self._active_task: asyncio.Task[Any] | None = None
        self._active_source: str | None = None
        self._lock = asyncio.Lock()
        self.online = True

    async def motion(self, command: MotionCommand, *, source: str) -> GatewayResult:
        cid = self._id(source)
        if not await self._prepare(source, cid):
            return self._offline(source, cid)
        if not self.mission_check():
            return GatewayResult("MISSION_REQUIRED", "An active mission is required for motion.", cid, source)
        try:
            clamped = MotionCommand(
                forward=max(-self.max_speed, min(self.max_speed, command.forward)),
                strafe=max(-self.max_speed, min(self.max_speed, command.strafe)),
                turn=max(-self.max_speed, min(self.max_speed, command.turn)),
            )
            result = await asyncio.to_thread(self.robot.move_analog, clamped)
            return self._result(result, source, cid, {"clamped": clamped != command} if clamped != command else None)
        except Exception:
            self.online = False
            return self._offline(source, cid)

    async def stop(self, *, source: str) -> GatewayResult:
        cid = self._id(source)
        await self._cancel_active()
        try:
            result = await asyncio.to_thread(self.robot.stop)
            return self._result(result, source, cid)
        except Exception:
            self.online = False
            return self._offline(source, cid)

    async def emergency_stop(self, *, source: str) -> GatewayResult:
        cid = self._id(source)
        await self._cancel_active()
        try:
            result = await asyncio.to_thread(self.robot.emergency_stop)
            if not getattr(self.robot, "connected", True):
                self.online = False
                return self._offline(source, cid)
            self.online = True
            return GatewayResult("ACCEPTED", "Emergency stop dispatched.", cid, source, result)
        except Exception:
            self.online = False
            return self._offline(source, cid)

    async def run_bounded(self, direction: str | Direction, seconds: float, speed: float,
                          *, source: str) -> GatewayResult:
        cid = self._id(source)
        if not await self._prepare(source, cid):
            return self._offline(source, cid)
        if not self.mission_check():
            return GatewayResult("MISSION_REQUIRED", "An active mission is required for motion.", cid, source)
        try:
            direction = Direction(direction)
            requested_seconds = float(seconds)
            requested_speed = float(speed)
            if not all(math.isfinite(v) for v in (requested_seconds, requested_speed)) or requested_seconds <= 0 or requested_speed < 0:
                raise ValueError("seconds must be positive and speed non-negative")
        except (ValueError, TypeError) as exc:
            return GatewayResult("INVALID", str(exc), cid, source)
        duration = min(requested_seconds, self.max_bounded_s)
        velocity = min(requested_speed, self.max_speed)
        axes = _axes(direction, velocity)
        async def run() -> GatewayResult:
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                if not self.online:
                    return self._offline(source, cid)
                result = await asyncio.to_thread(self.robot.move_analog, MotionCommand(**axes))
                if not result.ok:
                    if not getattr(self.robot, "connected", True):
                        self.online = False
                        return self._offline(source, cid)
                    return self._result(result, source, cid)
                await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
            result = await asyncio.to_thread(self.robot.stop)
            return self._result(result, source, cid, details={"clamped": duration != requested_seconds or velocity != requested_speed,
                "seconds": duration, "speed": velocity})
        return await self._run_owned(run(), source)

    async def run_operation(self, operation: Callable[[], Any], *, source: str) -> GatewayResult:
        """Run a cancellable voice/LLM operation under source arbitration."""
        cid = self._id(source)
        if not await self._prepare(source, cid):
            return self._offline(source, cid)
        async def invoke() -> Any:
            result = operation()
            return await result if asyncio.iscoroutine(result) else result
        try:
            value = await self._run_owned(invoke(), source)
            if isinstance(value, GatewayResult):
                return value
            return GatewayResult("ACCEPTED", "Operation completed.", cid, source,
                                 value if isinstance(value, dict) else None)
        except Exception as exc:
            return GatewayResult("FAILED", str(exc), cid, source)

    async def run_turn(self, degrees: float, speed: float = 0.5, *, source: str) -> GatewayResult:
        """Turn using measured yaw when available; otherwise use calibration."""
        cid = self._id(source)
        try:
            degrees, speed = float(degrees), float(speed)
            if not math.isfinite(degrees) or degrees == 0 or not math.isfinite(speed) or speed < 0:
                raise ValueError("degrees must be finite and nonzero; speed must be non-negative")
            before = await asyncio.to_thread(self.robot.read_telemetry)
            start_yaw = before.orientation.yaw
        except Exception:
            start_yaw = None
        if not await self._prepare(source, cid):
            return self._offline(source, cid)
        if not self.mission_check():
            return GatewayResult("MISSION_REQUIRED", "An active mission is required for motion.", cid, source)
        direction = Direction.TURN_LEFT if degrees > 0 else Direction.TURN_RIGHT
        speed = min(speed, self.max_speed)
        duration = min(abs(degrees) / self.turn_deg_per_s, self.max_bounded_s)
        async def turn() -> GatewayResult:
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                if start_yaw is not None:
                    try:
                        yaw = (await asyncio.to_thread(self.robot.read_telemetry)).orientation.yaw
                        if yaw is not None and abs((yaw - start_yaw + 180) % 360 - 180) >= abs(degrees):
                            break
                    except Exception:
                        pass
                axis = speed if direction == Direction.TURN_LEFT else -speed
                result = await asyncio.to_thread(self.robot.move_analog, MotionCommand(turn=axis))
                if not result.ok:
                    return self._result(result, source, cid)
                await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
            result = await asyncio.to_thread(self.robot.stop)
            return self._result(result, source, cid, {"clamped": duration < abs(degrees) / self.turn_deg_per_s})
        return await self._run_owned(turn(), source)

    async def _prepare(self, source: str, cid: str) -> bool:
        self._validate_source(source)
        if source == "hand" and self._active_source in {"voice", "llm"}:
            await self._cancel_active()
        elif source == "voice" and self._active_source == "llm":
            await self._cancel_active()
        return self.online and getattr(self.robot, "connected", True)

    async def _run_owned(self, awaitable: Any, source: str) -> GatewayResult:
        self._active_source = source
        self._active_task = asyncio.create_task(awaitable)
        task = self._active_task
        try:
            return await task
        except asyncio.CancelledError:
            return GatewayResult("CANCELLED", "Command cancelled by higher-priority input.", self._id(source), source)
        finally:
            if self._active_task is task:
                self._active_task = None
                self._active_source = None

    async def _cancel_active(self) -> None:
        task = self._active_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._active_task = None
        self._active_source = None

    def _id(self, source: str) -> str:
        self._validate_source(source)
        return str(uuid.uuid4())

    @staticmethod
    def _validate_source(source: str) -> None:
        if source not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")

    def _result(self, result: Any, source: str, cid: str, details: dict[str, Any] | None = None) -> GatewayResult:
        self.online = getattr(self.robot, "connected", True)
        if not self.online:
            return self._offline(source, cid)
        return GatewayResult(result.status.value.upper(), result.message, cid, source, details)

    @staticmethod
    def _offline(source: str, cid: str) -> GatewayResult:
        return GatewayResult("OFFLINE", "Robot is offline; command was not queued.", cid, source)


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _axes(direction: Direction, speed: float) -> dict[str, float]:
    return {Direction.FORWARD: {"forward": speed}, Direction.BACKWARD: {"forward": -speed},
            Direction.LEFT: {"strafe": speed}, Direction.RIGHT: {"strafe": -speed},
            Direction.TURN_LEFT: {"turn": speed}, Direction.TURN_RIGHT: {"turn": -speed}}.get(direction, {})
