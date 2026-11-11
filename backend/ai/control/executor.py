"""The brain loop: Claude sees each tool result and chooses the next step. Dry run for now.

Loop: model turn -> run its tool calls through :class:`GatewayLike` (``source="llm"``) -> send the
results back in ONE user message -> repeat until the model answers in text, or a limit is hit.

* Limits (``LoopLimits``): max tool calls (``LLM_MAX_STEPS``, 12), model turns (``LLM_MAX_TURNS``, 8)
  and wall clock (``LLM_TIMEOUT_S``, 60). Any of them -> ``stop()`` and ``state="limit"``.
* An invalid call is never executed; it goes back as an ``is_error`` tool result so the model can
  correct itself, and it counts toward the step cap.
* ``cancel()`` cancels the asyncio task, so it interrupts a model call or a move, then calls
  ``stop()``. No further model call is made. Refusal -> ``stop()`` + ``refused``; ``max_tokens`` or
  a model/gateway error -> ``stop()`` + ``failed``.

TODO(gateway #57): plug the real ``CommandGateway`` in where ``DryRunGateway`` is created
(``backend/ai/control/router.py``). The gateway owns arbitration, speed/duration limits and audit;
"stop", e-stop, hand input or a voice command must call ``TaskRunner.cancel()``.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.ai.control.brain import Brain, BrainError, initial_message
from backend.ai.control.tools import PlanValidationError, ToolCall, validate_call

logger = logging.getLogger(__name__)
SOURCE = "llm"
MAX_RESULT_CHARS = 4000  # tool results are sent to the model; keep them bounded


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
    duration of moves (0 = instant, 1 = real time); ``detections_data`` is what get_detections
    returns."""

    def __init__(self, time_scale: float = 1.0, detections_data: list[Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._time_scale = time_scale
        self._detections = list(detections_data or [])

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
        return list(self._detections)


@dataclass(frozen=True)
class LoopLimits:
    max_steps: int = 12
    max_turns: int = 8
    timeout_s: float = 60.0

    @classmethod
    def from_env(cls) -> "LoopLimits":
        def read(name: str, default: float, cast):
            try:
                value = cast(os.environ.get(name, default))
                return value if value > 0 else default
            except (TypeError, ValueError):
                return default

        return cls(read("LLM_MAX_STEPS", 12, int), read("LLM_MAX_TURNS", 8, int), read("LLM_TIMEOUT_S", 60.0, float))


@dataclass
class Step:
    tool: str
    args: Any
    state: str = "running"  # running | done | rejected | failed | skipped
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": self.args, "state": self.state,
                "result": self.result, "error": self.error}


@dataclass
class Task:
    id: int
    goal: str
    state: str = "running"  # running | done | cancelled | failed | limit | refused
    message: str | None = None  # why it ended (limit hit, error, refusal)
    answer: str | None = None  # the model's final text
    turns: int = 0
    steps: list[Step] = field(default_factory=list)
    _runner: "asyncio.Task[None] | None" = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "goal": self.goal, "state": self.state, "message": self.message,
                "answer": self.answer, "turns": self.turns, "steps": [s.to_dict() for s in self.steps]}


class _Limit(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _result_text(value: Any) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "...(truncated)"


class TaskRunner:
    """Holds at most one task. Starting a new one cancels (and stops for) the previous one."""

    def __init__(self, gateway: GatewayLike, brain: Brain, limits: LoopLimits | None = None) -> None:
        self._gateway = gateway
        self._brain = brain
        self._limits = limits or LoopLimits()
        self._ids = itertools.count(1)
        self.task: Task | None = None

    def snapshot(self) -> dict[str, Any]:
        return self.task.to_dict() if self.task else {"state": "idle"}

    async def start(self, goal: str) -> Task:
        self._brain.ensure_ready()  # BrainNotConfigured -> the router answers 503
        if self.task and self.task.state == "running":
            await self.cancel()
        task = Task(next(self._ids), goal)
        self.task = task
        task._runner = asyncio.create_task(self._run(task))
        return task

    async def cancel(self) -> bool:
        """Cancel during a model call or a step, then ``stop()``. False if nothing was running."""
        task = self.task
        if task is None or task.state != "running":
            return False
        task.state = "cancelled"
        runner = task._runner
        if runner is not None and not runner.done():
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        for step in task.steps:
            if step.state == "running":
                step.state = "skipped"
        await self._gateway.stop(source=SOURCE)
        logger.info("LLM task %s cancelled", task.id)
        return True

    async def wait(self) -> None:
        if self.task and self.task._runner:
            await asyncio.gather(self.task._runner, return_exceptions=True)

    # ------------------------------------------------------------------ the loop

    async def _end(self, task: Task, state: str, message: str | None = None) -> None:
        task.state, task.message = state, message
        if state != "done":
            await self._gateway.stop(source=SOURCE)

    async def _run(self, task: Task) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._limits.timeout_s

        def remaining() -> float:
            left = deadline - loop.time()
            if left <= 0:
                raise _Limit(f"time limit of {self._limits.timeout_s:g}s reached")
            return left

        try:
            messages = [initial_message(task.goal, await self._gateway.status(), await self._gateway.detections())]
            while True:
                if task.turns >= self._limits.max_turns:
                    raise _Limit(f"turn limit of {self._limits.max_turns} reached")
                task.turns += 1
                turn = await asyncio.wait_for(self._brain.turn(messages), remaining())

                if turn.stop_reason == "refusal":
                    return await self._end(task, "refused", "The assistant declined this request.")
                if turn.stop_reason == "max_tokens":
                    return await self._end(task, "failed", "model output was cut off")

                messages.append({"role": "assistant", "content": turn.content})  # verbatim, append-only
                if not turn.calls:
                    task.answer = turn.text
                    return await self._end(task, "done")

                results = []
                for call in turn.calls:
                    if len(task.steps) >= self._limits.max_steps:
                        raise _Limit(f"step limit of {self._limits.max_steps} reached")
                    results.append(await self._run_call(task, call, remaining))
                messages.append({"role": "user", "content": results})  # all results, one message, in order
        except _Limit as limit:
            await self._end(task, "limit", limit.message)
        except asyncio.TimeoutError:
            await self._end(task, "limit", f"time limit of {self._limits.timeout_s:g}s reached")
        except asyncio.CancelledError:
            raise  # cancel() owns the state change and the stop()
        except BrainError:
            await self._end(task, "failed", "model request failed")
        except Exception as exc:
            logger.warning("LLM task %s failed: %s", task.id, type(exc).__name__)
            await self._end(task, "failed", "a robot step failed")

    async def _run_call(self, task: Task, call: Any, remaining) -> dict[str, Any]:
        step = Step(call.name, call.input)
        task.steps.append(step)
        block = {"type": "tool_result", "tool_use_id": call.id}
        try:
            validated = validate_call(call.name, call.input)
        except PlanValidationError as exc:  # never executed; the model gets to correct it
            step.state, step.error = "rejected", str(exc)
            return {**block, "is_error": True, "content": f"Invalid call, not executed: {exc}"}
        try:
            step.result = await asyncio.wait_for(self._dispatch(validated), remaining())
        except (asyncio.TimeoutError, asyncio.CancelledError):
            step.state = "skipped"
            raise
        except Exception:
            step.state = "failed"
            raise
        step.state = "done"
        return {**block, "content": _result_text(step.result)}

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
        raise ValueError(f"unknown tool {call.name}")  # unreachable: calls are validated
