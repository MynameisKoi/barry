"""Async command gateway over the synchronous RobotInterface."""

from __future__ import annotations

import asyncio
import contextvars
import math
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from backend.robot.factory import create_robot
from backend.robot.interface import Direction, MotionCommand, Posture, RobotInterface
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
                 turn_deg_per_s: float | None = None, live_detections: Any = None) -> None:
        self.robot = robot or create_robot()
        self.mission_check = mission_check or (lambda: bool(mission_service.get_status().get("active")))
        self.max_speed = _env_float("GATEWAY_MAX_SPEED", 0.6) if max_speed is None else max_speed
        self.max_bounded_s = _env_float("GATEWAY_MAX_BOUNDED_S", 5.0) if max_bounded_s is None else max_bounded_s
        self.turn_deg_per_s = _env_float("ROBOT_TURN_DEG_PER_S", 90.0) if turn_deg_per_s is None else turn_deg_per_s
        self._active_task: asyncio.Task[Any] | None = None
        self._active_source: str | None = None
        self._arbitration_lock = asyncio.Lock()
        self._commanded_posture = Posture()
        self.posture_settle_s = _env_float("POSTURE_SETTLE_S", 0.5)
        self.hand_hold_s = _env_float("GATEWAY_HAND_HOLD_S", 2.0)
        self._last_hand_at = 0.0
        try:
            self._emergency_stopped = bool(self.robot.get_control_state().emergency_stopped)
        except Exception:
            self._emergency_stopped = False
        self._preempt_callbacks: list[Callable[[str], Any]] = []
        self.online = True
        self._command_id_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"gateway_command_id_{id(self)}", default=None
        )
        # backend.gateway.detections.LiveDetections (or None = no detector wired).
        self.live_detections = live_detections

    def _mission_required(self, source: str) -> bool:
        """Determines if an active mission is required.
        When REQUIRE_ACTIVE_MISSION is disabled (default for frictionless testing / teammate onboarding),
        all sources (hand, voice, llm) operate with full access without requiring an active mission.
        """
        require_mission = os.environ.get("REQUIRE_ACTIVE_MISSION", "false").lower() in ("1", "true", "yes")
        if not require_mission:
            return False
        return source in {"voice", "llm"} and not self.mission_check()

    async def motion(self, command: MotionCommand, *, source: str) -> GatewayResult:
        cid = self._id(source)
        if source in {"hand", "voice"}:
            self._notify_preempt(source)
        blocked = await self._prepare(source, cid)
        if blocked:
            return blocked
        posture_block = None if command.is_zero else await self._posture_motion_block(source, cid)
        if posture_block:
            return posture_block
        if self._emergency_stopped:
            return GatewayResult("ESTOP_ACTIVE", "Emergency stop is latched.", cid, source)
        if self._mission_required(source):
            return GatewayResult("MISSION_REQUIRED", "An active mission is required for motion.", cid, source)
        try:
            clamped = MotionCommand(
                forward=max(-self.max_speed, min(self.max_speed, command.forward)),
                strafe=max(-self.max_speed, min(self.max_speed, command.strafe)),
                turn=max(-self.max_speed, min(self.max_speed, command.turn)),
            )
            async def send() -> GatewayResult:
                result = await self._robot_call("move_analog", clamped, source=source, cid=cid)
                return self._result(result, source, cid, {"clamped": clamped != command} if clamped != command else None)
            outcome = await self._run_owned(send(), source, cid)
            if source == "hand" and outcome.status not in {"BUSY", "OFFLINE"}:
                self._last_hand_at = time.monotonic()
            return outcome
        except Exception:
            self.online = False
            return self._offline(source, cid)

    async def stop(self, *, source: str) -> GatewayResult:
        cid = self._id(source)
        self._notify_preempt(source)
        await self._cancel_active()
        if source == "hand":
            self._last_hand_at = time.monotonic()
        try:
            result = await self._robot_call("stop", source=source, cid=cid)
            details = {"intensity": result.intensity.value} if getattr(result, "intensity", None) else None
            return self._result(result, source, cid, details)
        except Exception:
            self.online = False
            return self._offline(source, cid)

    async def emergency_stop(self, *, source: str) -> GatewayResult:
        cid = self._id(source)
        self._notify_preempt(source)
        await self._cancel_active()
        try:
            result = await self._robot_call("emergency_stop", source=source, cid=cid)
            if not getattr(self.robot, "connected", True):
                self.online = False
                return self._offline(source, cid)
            self.online = True
            self._emergency_stopped = True
            return GatewayResult("ACCEPTED", "Emergency stop dispatched.", cid, source, result)
        except Exception:
            self.online = False
            return self._offline(source, cid)

    async def run_bounded(self, direction: str | Direction, seconds: float, speed: float,
                          *, source: str) -> GatewayResult:
        cid = self._id(source)
        if source in {"hand", "voice"}:
            self._notify_preempt(source)
        blocked = await self._prepare(source, cid)
        if blocked:
            return blocked
        posture_block = await self._posture_motion_block(source, cid)
        if posture_block:
            return posture_block
        if self._emergency_stopped:
            return GatewayResult("ESTOP_ACTIVE", "Emergency stop is latched.", cid, source)
        if self._mission_required(source):
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
        if source == "hand":
            self._last_hand_at = time.monotonic()
        async def run() -> GatewayResult:
            deadline = time.monotonic() + duration
            try:
                while time.monotonic() < deadline:
                    if not self.online:
                        return self._offline(source, cid)
                    result = await self._robot_call("move_analog", MotionCommand(**axes), source=source, cid=cid)
                    if not result.ok:
                        if not getattr(self.robot, "connected", True):
                            self.online = False
                            return self._offline(source, cid)
                        return self._result(result, source, cid)
                    await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
                result = await self._robot_call("stop", source=source, cid=cid)
                return self._result(result, source, cid, details={"clamped": duration != requested_seconds or velocity != requested_speed,
                    "seconds": duration, "speed": velocity})
            except asyncio.CancelledError:
                await self._robot_call("stop", source=source, cid=cid)
                raise
        return await self._run_owned(run(), source, cid)

    async def run_operation(self, operation: Callable[[], Any], *, source: str) -> GatewayResult:
        """Run a cancellable voice/LLM operation under source arbitration."""
        cid = self._id(source)
        blocked = await self._prepare(source, cid)
        if blocked:
            return blocked
        async def invoke() -> Any:
            result = operation()
            return await result if asyncio.iscoroutine(result) else result
        try:
            value = await self._run_owned(invoke(), source, cid)
            if isinstance(value, GatewayResult):
                return value
            return GatewayResult("ACCEPTED", "Operation completed.", cid, source,
                                 value if isinstance(value, dict) else None)
        except Exception as exc:
            return GatewayResult("FAILED", str(exc), cid, source)

    def on_preempt(self, callback: Callable[[str], Any]) -> None:
        self._preempt_callbacks.append(callback)

    def notify_preempt(self, source: str) -> None:
        """Notify task owners when an external hand/voice input takes priority."""
        self._notify_preempt(source)

    async def set_style(self, intensity: str, gait: str, *, source: str) -> GatewayResult:
        cid = self._id(source)
        if source in {"hand", "voice"}:
            self._notify_preempt(source)
        blocked = await self._prepare(source, cid)
        if blocked:
            return blocked
        if self._emergency_stopped:
            return GatewayResult("ESTOP_ACTIVE", "Emergency stop is latched.", cid, source)
        from backend.robot.interface import Gait, Intensity, MovementStyle
        try:
            style = MovementStyle(Intensity(intensity), Gait(gait))
            async def apply() -> GatewayResult:
                result = await self._robot_call("set_movement_style", style, source=source, cid=cid)
                return self._result(result, source, cid)
            return await self._run_owned(apply(), source, cid)
        except (ValueError, TypeError) as exc:
            return GatewayResult("INVALID", str(exc), cid, source)

    async def run_action(self, action_id: int, *, source: str) -> GatewayResult:
        cid = self._id(source)
        if source in {"hand", "voice"}:
            self._notify_preempt(source)
        blocked = await self._prepare(source, cid)
        if blocked:
            return blocked
        if self._emergency_stopped:
            return GatewayResult("ESTOP_ACTIVE", "Emergency stop is latched.", cid, source)
        if self._mission_required(source):
            return GatewayResult("MISSION_REQUIRED", "An active mission is required for robot actions.", cid, source)
        async def run() -> GatewayResult:
            try:
                result = await self._robot_call("perform_action", int(action_id), source=source, cid=cid)
                return self._result(result, source, cid)
            except asyncio.CancelledError:
                await self._robot_call("stop", source=source, cid=cid)
                raise
        return await self._run_owned(run(), source, cid)

    async def status(self, *, source: str = "llm") -> dict[str, Any]:
        try:
            cid = self._id(source)
            telemetry = await self._robot_call("read_telemetry", source=source, cid=cid)
            control = await self._robot_call("get_control_state", source=source, cid=cid)
            return {"control_state": control.to_dict(), "telemetry": telemetry.to_dict(),
                    "online": bool(self.online and self.robot.connected),
                    "active_source": self._active_source, "emergency_stopped": self._emergency_stopped,
                    "detector": self._detector_state()}
        except Exception:
            self.online = False
            return {"control_state": {}, "telemetry": {}, "online": False,
                    "active_source": self._active_source, "emergency_stopped": self._emergency_stopped,
                    "detector": self._detector_state()}

    def _detector_state(self) -> str:
        source = self.live_detections
        return source.state() if source is not None else "unavailable"

    async def detections(self) -> list[Any]:
        """Live YOLO detections from the newest shared-camera frame, each with ``age_s``; ``[]`` when
        there is no model/camera, the frame is stale, or inference failed. Never simulated."""
        source = self.live_detections
        if source is None:
            return []
        try:
            return await source.detections()
        except Exception:
            return []

    def _notify_preempt(self, source: str) -> None:
        if source in {"hand", "voice", "emergency"}:
            for callback in tuple(self._preempt_callbacks):
                try:
                    value = callback(source)
                    if asyncio.iscoroutine(value):
                        asyncio.create_task(value)
                except Exception:
                    continue

    async def run_turn(self, degrees: float, speed: float = 0.5, *, source: str) -> GatewayResult:
        """Turn by degrees; positive is right, negative is left (per LLM tools)."""
        cid = self._id(source)
        if source in {"hand", "voice"}:
            self._notify_preempt(source)
        try:
            degrees, speed = float(degrees), float(speed)
            if not math.isfinite(degrees) or degrees == 0 or not math.isfinite(speed) or speed < 0:
                raise ValueError("degrees must be finite and nonzero; speed must be non-negative")
        except (TypeError, ValueError) as exc:
            return GatewayResult("INVALID", str(exc), cid, source)
        try:
            start_yaw = (await self._robot_call("read_telemetry", source=source, cid=cid)).orientation.yaw
        except Exception:
            start_yaw = None
        blocked = await self._prepare(source, cid)
        if blocked:
            return blocked
        posture_block = await self._posture_motion_block(source, cid)
        if posture_block:
            return posture_block
        if self._emergency_stopped:
            return GatewayResult("ESTOP_ACTIVE", "Emergency stop is latched.", cid, source)
        if self._mission_required(source):
            return GatewayResult("MISSION_REQUIRED", "An active mission is required for motion.", cid, source)
        direction = Direction.TURN_RIGHT if degrees > 0 else Direction.TURN_LEFT
        speed = min(speed, self.max_speed)
        duration = min(abs(degrees) / self.turn_deg_per_s, self.max_bounded_s)
        async def turn() -> GatewayResult:
            deadline = time.monotonic() + duration
            try:
                while time.monotonic() < deadline:
                    if start_yaw is not None:
                        try:
                            yaw = (await self._robot_call("read_telemetry", source=source, cid=cid)).orientation.yaw
                            delta = (yaw - start_yaw + 180) % 360 - 180 if yaw is not None else None
                            # Robot yaw follows the wire convention: positive is left,
                            # while the LLM tool contract defines positive degrees as right.
                            if delta is not None and math.copysign(1, delta) == math.copysign(1, -degrees) and abs(delta) >= abs(degrees):
                                break
                        except Exception:
                            pass
                    axis = speed if direction == Direction.TURN_LEFT else -speed
                    result = await self._robot_call("move_analog", MotionCommand(turn=axis), source=source, cid=cid)
                    if not result.ok:
                        return self._result(result, source, cid)
                    await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
                result = await self._robot_call("stop", source=source, cid=cid)
                return self._result(result, source, cid, {"clamped": duration < abs(degrees) / self.turn_deg_per_s})
            except asyncio.CancelledError:
                await self._robot_call("stop", source=source, cid=cid)
                raise
        return await self._run_owned(turn(), source, cid)

    async def reset_emergency_stop(self, *, source: str) -> GatewayResult:
        cid = self._id(source)
        try:
            result = await self._robot_call("reset_emergency_stop", source=source, cid=cid)
            if not getattr(self.robot, "connected", True):
                self.online = False
                return self._offline(source, cid)
            self._emergency_stopped = False
            return GatewayResult("ACCEPTED" if result.get("success") else "REJECTED",
                                 str(result.get("message", "Emergency stop reset.")), cid, source, result)
        except Exception:
            self.online = False
            return self._offline(source, cid)

    async def _prepare(self, source: str, cid: str) -> GatewayResult | None:
        self._validate_source(source)
        if source in {"voice", "llm"} and time.monotonic() - self._last_hand_at < self.hand_hold_s:
            return GatewayResult("BUSY", "Hand control has priority during its hold window.", cid, source)
        if not self.online and getattr(self.robot, "connected", False):
            self.online = True
        if not self.online or not getattr(self.robot, "connected", True):
            return self._offline(source, cid)
        return None

    async def _robot_call(self, method: str, *args: Any, source: str, cid: str) -> Any:
        def invoke() -> Any:
            context = getattr(self.robot, "command_context", None)
            if context is None:
                return getattr(self.robot, method)(*args)
            with context(source, cid):
                return getattr(self.robot, method)(*args)
        return await asyncio.to_thread(invoke)

    async def move_discrete(self, direction: str | Direction, intensity: Any = None, *, source: str) -> GatewayResult:
        cid = self._id(source)
        if source in {"hand", "voice"}:
            self._notify_preempt(source)
        blocked = await self._prepare(source, cid)
        if blocked:
            return blocked
        posture_block = await self._posture_motion_block(source, cid)
        if posture_block:
            return posture_block
        if self._emergency_stopped:
            return GatewayResult("ESTOP_ACTIVE", "Emergency stop is latched.", cid, source)
        if self._mission_required(source):
            return GatewayResult("MISSION_REQUIRED", "An active mission is required for motion.", cid, source)
        try:
            direction = Direction(direction)
            from backend.robot.interface import Intensity
            level = Intensity(intensity) if intensity is not None else None
        except (ValueError, TypeError) as exc:
            return GatewayResult("INVALID", str(exc), cid, source)
        async def run() -> GatewayResult:
            result = await self._robot_call("move_discrete", direction, level, source=source, cid=cid)
            return self._result(result, source, cid)
        return await self._run_owned(run(), source, cid)

    async def _posture_motion_block(self, source: str, cid: str) -> GatewayResult | None:
        if self._emergency_stopped:
            return GatewayResult("ESTOP_ACTIVE", "Emergency stop is latched.", cid, source)
        posture = self._commanded_posture
        if posture.pitch or posture.roll or posture.height.value != "medium":
            if source == "hand":
                result = await self.level_posture(source="hand")
                if result.status == "ACCEPTED":
                    logging.getLogger(__name__).info("auto-levelled before hand motion; command_id=%s", cid)
                    return None
                return result
            return GatewayResult("INVALID", "Level posture to medium height before motion (level_posture).", cid, source)
        return None

    async def level_posture(self, *, source: str) -> GatewayResult:
        """Restore zero attitude and medium height, then wait for posture settling."""
        return await self.set_posture({"height": "medium", "pitch": 0}, source=source)

    async def set_posture(self, values: dict[str, Any], *, source: str) -> GatewayResult:
        """Set body attitude (degrees) and height in one command.

        Pitch is forwarded unchanged to SDK attitude('p'); code does not establish
        whether positive or negative pitch points the mounted camera down. Tom must
        verify the physical sign. Simulator posture applies immediately; the default
        POSTURE_SETTLE_S=0.5 is a conservative, uncalibrated hardware estimate.
        Motion requires commanded zero pitch/roll and medium height (yaw ignored).
        Hand motion levels and settles automatically; voice/LLM must level first.
        """
        cid = self._id(source)
        blocked = await self._prepare(source, cid)
        if blocked:
            return blocked
        if self._emergency_stopped:
            return GatewayResult("ESTOP_ACTIVE", "Emergency stop is latched.", cid, source)
        try:
            from backend.robot.interface import HeightPreset, Posture
            target = Posture(HeightPreset(values["height"]), values.get("roll", 0),
                             values.get("pitch", 0), values.get("yaw", 0))
        except (ValueError, TypeError, KeyError) as exc:
            return GatewayResult("INVALID", str(exc), cid, source)
        from backend.robot.config import check_attitude
        reason = check_attitude(target.roll, target.pitch, target.yaw)
        if reason:
            return GatewayResult("INVALID", reason, cid, source)
        async def apply() -> GatewayResult:
            if self.robot.get_control_state().moving:
                stopped = await self._robot_call("stop", source=source, cid=cid)
                if not stopped.ok:
                    return self._result(stopped, source, cid)
            result = await self._robot_call("set_posture", target, source=source, cid=cid)
            outcome = self._result(result, source, cid)
            if result.ok:
                self._commanded_posture = target
                await asyncio.sleep(self.posture_settle_s)
            return outcome
        return await self._run_owned(apply(), source, cid)

    async def reset(self, *, source: str) -> GatewayResult:
        cid = self._id(source)
        await self._cancel_active()
        result = await self._robot_call("reset", source=source, cid=cid)
        return self._result(result, source, cid)

    async def list_actions(self, *, source: str = "hand") -> list[Any]:
        cid = self._id(source)
        return await self._robot_call("list_actions", source=source, cid=cid)

    async def with_command_id(self, command_id: str | None, operation: Callable[[], Any]) -> Any:
        """Run a command using a sanitized caller ID, while retaining generated IDs otherwise."""
        if not command_id:
            return await operation()
        token = self._command_id_override.set(command_id)
        try:
            return await operation()
        finally:
            self._command_id_override.reset(token)

    async def _run_owned(self, awaitable: Any, source: str, cid: str) -> GatewayResult:
        async with self._arbitration_lock:
            current = self._active_task
            if current and not current.done():
                current_priority = _priority(self._active_source)
                requested_priority = _priority(source)
                if requested_priority < current_priority:
                    if hasattr(awaitable, "close"):
                        awaitable.close()
                    return GatewayResult("BUSY", f"{self._active_source} has control.", cid, source)
                current.cancel()
                try:
                    await current
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            task = asyncio.create_task(awaitable)
            self._active_task = task
            self._active_source = source
        try:
            return await task
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if (current_task and current_task.cancelling()) or not task.cancelled():
                raise
            return GatewayResult("CANCELLED", "Command cancelled by higher-priority input.", cid, source)
        finally:
            if self._active_task is task:
                self._active_task = None
                self._active_source = None

    async def _cancel_active(self) -> None:
        async with self._arbitration_lock:
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
        return self._command_id_override.get() or str(uuid.uuid4())

    @staticmethod
    def _validate_source(source: str) -> None:
        if source not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")

    def _result(self, result: Any, source: str, cid: str, details: dict[str, Any] | None = None) -> GatewayResult:
        self.online = getattr(self.robot, "connected", True)
        if not self.online:
            return self._offline(source, cid)
        if getattr(result, "http_status", None) is not None:
            details = {**(details or {}), "http_status": result.http_status}
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


def _priority(source: str | None) -> int:
    return {"llm": 1, "voice": 2, "hand": 3, "emergency": 3}.get(source, 0)
