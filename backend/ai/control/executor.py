"""Runs a validated plan step by step through a gateway. No confirm step (Tom's decision).

The executor only knows the small :class:`GatewayLike` protocol and always passes
``source="llm"``. Today it runs against :class:`DryRunGateway`, which records calls and moves
nothing.

TODO(gateway #57): plug the real ``CommandGateway`` in where ``DryRunGateway`` is created
(``backend/ai/control/router.py``). The gateway owns arbitration, speed/duration limits and
audit; "stop", e-stop, hand input or a voice command must call ``TaskRunner.cancel()``.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.ai.control.tools import ToolCall

logger = logging.getLogger(__name__)
SOURCE = "llm"


class GatewayLike(Protocol):
    async def run_bounded(self, direction: str, seconds: float, speed: float, *, source: str) -> Any: ...
    async def turn(self, degrees: float, *, source: str) -> Any: ...
    async def stop(self, *, source: str) -> Any: ...
    async def set_style(self, intensity: str, gait: str, *, source: str) -> Any: ...
    async def run_action(self, action_id: int, *, source: str) -> Any: ...
    async def status(self) -> dict[str, Any]: ...
    async def detections(self) -> list[Any]: ...


class DryRunGateway:
    """Records every call and sends nothing to the robot. ``time_scale`` scales the simulated
    duration of moves (0 = instant, 1 = real time)."""

    def __init__(self, time_scale: float = 1.0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._time_scale = time_scale

    def _rec(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((name, kwargs))
        return {"dry_run": True, "ok": True}

    async def run_bounded(self, direction: str, seconds: float, speed: float, *, source: str) -> Any:
        result = self._rec("run_bounded", direction=direction, seconds=seconds, speed=speed, source=source)
        await asyncio.sleep(seconds * self._time_scale)
        return result

    async def turn(self, degrees: float, *, source: str) -> Any:
        return self._rec("turn", degrees=degrees, source=source)

    async def stop(self, *, source: str) -> Any:
        return self._rec("stop", source=source)

    async def set_style(self, intensity: str, gait: str, *, source: str) -> Any:
        return self._rec("set_style", intensity=intensity, gait=gait, source=source)

    async def run_action(self, action_id: int, *, source: str) -> Any:
        return self._rec("run_action", action_id=action_id, source=source)

    async def status(self) -> dict[str, Any]:
        return {"dry_run": True, "state": "idle"}

    async def detections(self) -> list[Any]:
        return []


@dataclass
class Step:
    call: ToolCall
    state: str = "pending"  # pending | running | done | failed | skipped
    result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.call.name, "args": self.call.args, "state": self.state, "result": self.result}


@dataclass
class Task:
    id: int
    goal: str
    steps: list[Step]
    state: str = "running"  # running | done | cancelled | failed
    error: str | None = None
    current: int | None = None
    _runner: "asyncio.Task[None] | None" = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "goal": self.goal, "state": self.state, "error": self.error,
                "current_step": self.current, "steps": [s.to_dict() for s in self.steps]}


class TaskRunner:
    """Holds at most one task. Starting a new one cancels (and stops for) the previous one."""

    def __init__(self, gateway: GatewayLike) -> None:
        self._gateway = gateway
        self._ids = itertools.count(1)
        self.task: Task | None = None

    def snapshot(self) -> dict[str, Any]:
        return self.task.to_dict() if self.task else {"state": "idle"}

    async def start(self, goal: str, plan: list[ToolCall]) -> Task:
        if self.task and self.task.state == "running":
            await self.cancel()
        task = Task(next(self._ids), goal, [Step(c) for c in plan])
        self.task = task
        task._runner = asyncio.create_task(self._run(task))
        return task

    async def cancel(self) -> bool:
        """Cancel between/within steps and always call ``stop()``. False if nothing was running."""
        task = self.task
        if task is None or task.state != "running":
            return False
        task.state = "cancelled"
        runner = task._runner
        if runner is not None and not runner.done():
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        for step in task.steps:
            if step.state in ("pending", "running"):
                step.state = "skipped"
        await self._gateway.stop(source=SOURCE)
        logger.info("LLM task %s cancelled", task.id)
        return True

    async def wait(self) -> None:
        if self.task and self.task._runner:
            await asyncio.gather(self.task._runner, return_exceptions=True)

    async def _run(self, task: Task) -> None:
        try:
            for i, step in enumerate(task.steps):
                if task.state != "running":  # cancelled between steps
                    return
                task.current = i
                step.state = "running"
                step.result = await self._dispatch(step.call)
                step.state = "done"
            task.state = "done"
            task.current = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("LLM task %s failed at step %s: %s", task.id, task.current, type(exc).__name__)
            task.state, task.error = "failed", f"step {task.current} failed"
            task.steps[task.current].state = "failed"
            await self._gateway.stop(source=SOURCE)

    async def _dispatch(self, call: ToolCall) -> Any:
        g, a = self._gateway, call.args
        if call.name == "move":
            return await g.run_bounded(a["direction"], a["seconds"], a["speed"], source=SOURCE)
        if call.name == "turn":
            return await g.turn(a["degrees"], source=SOURCE)
        if call.name == "stop":
            return await g.stop(source=SOURCE)
        if call.name == "set_style":
            return await g.set_style(a["intensity"], a["gait"], source=SOURCE)
        if call.name == "run_action":
            return await g.run_action(a["action_id"], source=SOURCE)
        if call.name == "get_status":
            return await g.status()
        if call.name == "get_detections":
            return await g.detections()
        raise ValueError(f"unknown tool {call.name}")  # unreachable: plans are validated
