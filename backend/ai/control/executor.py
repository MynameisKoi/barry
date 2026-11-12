"""The brain loop: Claude sees each tool result and chooses the next step.

Loop: model turn -> run its tool calls through :class:`GatewayLike` (``source="llm"``) -> send the
results back in ONE user message -> repeat until the model answers in text, or a limit is hit.

* Limits (``LoopLimits``): max tool calls (``LLM_MAX_STEPS``, 12), model turns (``LLM_MAX_TURNS``, 8)
  and wall clock (``LLM_TIMEOUT_S``, 60). Any of them -> ``stop()`` and ``state="limit"``.
* An invalid call is never executed; it goes back as an ``is_error`` tool result so the model can
  correct itself, and it counts toward the step cap.
* ``cancel()`` cancels the asyncio task, so it interrupts a model call or a move, then calls
  ``stop()``. No further model call is made. Refusal -> ``stop()`` + ``refused``; ``max_tokens`` or
  a model/gateway error -> ``stop()`` + ``failed``.

Find mode (Phase 6, "find an object"): the same loop with the find tool set and tight limits that are ENFORCED HERE
(a rejected call is never executed, whatever the model says): move <= 1 s at <= 0.3, turn <= 45 deg, a look()
before the first motion and between any two motions, 120 s per run. look() hands Claude a downscaled camera image
in the tool_result. The run ends by itself, ALWAYS followed by a stop, when found() is called, when YOLO reports
the target at >= 0.5 confidence, on timeout, on camera loss (no frame, or older than 2 s), refusal or error.

The router supplies the real gateway adapter by default. Gateway preemption cancels this whole
task synchronously, so no later tool step can run after hand, voice-stop or e-stop input.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from backend.ai.control.brain import Brain, BrainError, find_initial_message, initial_message
from backend.ai.control.look import CameraLost, capture_look
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
    async def set_posture(self, height: str, pitch: float, *, source: str) -> Any: ...
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

    async def set_posture(self, height: str, pitch: float, *, source: str) -> Any:
        return self._rec("set_posture", height=height, pitch=pitch, source=source)

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
        return cls(_env_number("LLM_MAX_STEPS", 12, int), _env_number("LLM_MAX_TURNS", 8, int),
                   _env_number("LLM_TIMEOUT_S", 60.0, float))


@dataclass(frozen=True)
class FindLimits(LoopLimits):
    """Autonomous find-an-object runs: 120 s, and more turns/steps because each move is tiny."""

    max_steps: int = 60
    max_turns: int = 40
    timeout_s: float = 120.0
    min_confidence: float = 0.5   # YOLO confidence at which the target counts as found
    session_s: float = 300.0      # the whole find session: searches + watching (LLM_FIND_SESSION_S)
    lost_after_s: float = 2.0     # target unseen for longer than this while WATCHING -> a new search
    watch_interval_s: float = 1.0 # WATCH polls YOLO about once per second (no Claude call)
    settle_s: float = 0.5         # body settle time after a posture change, before the photo

    @classmethod
    def from_env(cls) -> "FindLimits":
        return cls(_env_number("LLM_FIND_MAX_STEPS", 60, int), _env_number("LLM_FIND_MAX_TURNS", 40, int),
                   _env_number("LLM_FIND_TIMEOUT_S", 120.0, float), _env_number("LLM_FIND_CONFIDENCE", 0.5, float),
                   _env_number("LLM_FIND_SESSION_S", 300.0, float), _env_number("LLM_FIND_LOST_S", 2.0, float),
                   _env_number("LLM_FIND_WATCH_S", 1.0, float), _env_number("LLM_FIND_SETTLE_S", 0.5, float))


def _env_number(name: str, default: float, cast):
    try:
        value = cast(os.environ.get(name, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


FIND_MAX_WORDS = 3   # "tennis ball", "potted plant", "teddy bear"; a sentence is not an object


def valid_find_object(raw: str) -> bool:
    """Letters, single spaces, - and ' only, 1-40 characters, at most 3 words. Checked on the RAW text, so a newline or
    other control character is rejected instead of being collapsed into a space."""
    return bool(FIND_OBJECT_RE.match(raw or "")) and len(raw.split()) <= FIND_MAX_WORDS and "  " not in raw


FIND_OBJECT_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,39}$")
_FIND_GOAL_RE = re.compile(r"^(?:please\s+)?find\s+(?:me\s+)?(?:(?:a|an|the|some|my)\s+)?(?!out\b)(?P<obj>[a-z][a-z '\-]{0,39})$")
# What YOLO (COCO) calls things people name differently.
_ALIASES = {"ball": {"sports ball"}, "football": {"sports ball"}, "soccer ball": {"sports ball"},
            "tennis ball": {"sports ball"}, "basketball": {"sports ball"}, "sofa": {"couch"}, "couch": {"couch"}, "television": {"tv"}, "monitor": {"tv"}, "mug": {"cup"},
            "phone": {"cell phone"}, "cellphone": {"cell phone"}, "plant": {"potted plant"}, "table": {"dining table"},
            "human": {"person"}, "people": {"person"}, "man": {"person"}, "woman": {"person"}}


def parse_find_goal(goal: str) -> str | None:
    """"Find a chair" / "please find the bottle." -> "chair"; anything else (including "find out ...") -> None."""
    m = _FIND_GOAL_RE.match(" ".join((goal or "").lower().split()).rstrip(".!?"))
    obj = m.group("obj").strip() if m else None
    return obj if obj and len(obj.split()) <= FIND_MAX_WORDS else None


def target_labels(target: str) -> set[str]:
    target = target.lower().strip()
    return {target} | _ALIASES.get(target, set())


def describe_where(detection: dict[str, Any]) -> str:
    """"ahead, close (confidence 0.87)" from a YOLO box [ymin, xmin, ymax, xmax] in 0-1."""
    box = detection.get("box") or detection.get("bounding_box") or []
    side = "ahead"
    size = ""
    if isinstance(box, (list, tuple)) and len(box) == 4:
        centre = (float(box[1]) + float(box[3])) / 2
        side = "to the left" if centre < 0.35 else "to the right" if centre > 0.65 else "ahead"
        size = ", close" if float(box[2]) - float(box[0]) > 0.6 else ", far" if float(box[2]) - float(box[0]) < 0.25 else ""
    confidence = detection.get("confidence")
    tail = f" (confidence {float(confidence):.2f})" if isinstance(confidence, (int, float)) else ""
    return f"{side}{size}{tail}"


@dataclass
class Step:
    tool: str
    args: Any
    state: str = "running"  # running | done | rejected | failed | skipped
    result: Any = None
    error: str | None = None
    note: str | None = None  # the model's short reason, from the text it wrote before the call

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": self.args, "state": self.state,
                "result": self.result, "error": self.error, "note": self.note}


@dataclass
class Task:
    id: int
    goal: str
    state: str = "running"  # running | done | cancelled | failed | limit | refused
    message: str | None = None  # why it ended (limit hit, error, refusal)
    answer: str | None = None  # the model's final text
    turns: int = 0
    steps: list[Step] = field(default_factory=list)
    mode: str = "plan"  # "plan" | "find"
    target: str | None = None
    outcome: str | None = None  # find runs: found | not_found | timeout | camera_lost | limit | refused | error | cancelled
    found: dict[str, Any] | None = None
    found_snapshot: str | None = None  # base64 JPEG data URL of the snapshot where target was spotted
    deadline: float | None = None
    timeout_s: float | None = None
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    looks: int = 0
    started_at: float = 0.0
    ended_at: float | None = None
    looked: bool = False  # a look() since the last motion (find mode enforces this)
    phase: str | None = None  # find sessions: searching | watching | lost
    searches: int = 0
    session_deadline: float | None = None
    session_s: float | None = None
    pitch: float = 0.0  # the body posture the executor last applied (level / medium = 0 / "medium")
    height: str = "medium"
    _last_look_jpeg: bytes | None = field(default=None, repr=False)
    _finish: tuple[str, str] | None = field(default=None, repr=False)
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _runner: "asyncio.Task[None] | None" = field(default=None, repr=False)

    def seconds_left(self) -> float | None:
        if self.deadline is None or self.state != "running":
            return None
        return round(max(0.0, self.deadline - self._clock()), 1)

    @property
    def posture_dirty(self) -> bool:
        return self.pitch != 0.0 or self.height != "medium"

    def session_left(self) -> float | None:
        if self.session_deadline is None or self.state != "running":
            return None
        return round(max(0.0, self.session_deadline - self._clock()), 1)

    def to_dict(self) -> dict[str, Any]:
        data = {"id": self.id, "goal": self.goal, "state": self.state, "message": self.message,
                "answer": self.answer, "turns": self.turns, "steps": [s.to_dict() for s in self.steps]}
        if self.mode == "find":
            data.update({"mode": "find", "target": self.target, "outcome": self.outcome, "found": self.found,
                         "found_snapshot": self.found_snapshot,
                         "seconds_left": self.seconds_left(), "timeout_s": self.timeout_s,
                         "model_calls": self.model_calls, "input_tokens": self.input_tokens,
                         "output_tokens": self.output_tokens, "phase": self.phase, "searches": self.searches,
                         "session_left": self.session_left(), "session_s": self.session_s,
                         "posture": {"pitch_deg": self.pitch, "height": self.height}})
        return data

    def summary(self) -> dict[str, Any]:
        """One line per run for the event log."""
        ended = self.ended_at if self.ended_at is not None else self._clock()
        return {"task_id": self.id, "mode": self.mode, "goal": self.goal, "target": self.target, "state": self.state,
                "outcome": self.outcome, "message": self.message, "duration_s": round(max(0.0, ended - self.started_at), 1),
                "steps": len(self.steps), "looks": self.looks, "searches": self.searches, "turns": self.turns, "model_calls": self.model_calls,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


class _Limit(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _CameraLost(Exception):
    pass


class _Refused(Exception):
    pass


class FindUnsupported(RuntimeError):
    """The configured brain has no find mode."""


def _result_text(value: Any) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "...(truncated)"


class TaskRunner:
    """Holds at most one task. Starting a new one cancels (and stops for) the previous one."""

    def __init__(self, gateway: GatewayLike, brain: Brain, limits: LoopLimits | None = None, *,
                 camera: Callable[[], Any] | Any = None, find_limits: FindLimits | None = None,
                 clock: Callable[[], float] | None = None,
                 run_logger: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._gateway = gateway
        self._brain = brain
        self._limits = limits or LoopLimits()
        self._find_limits = find_limits or FindLimits()
        self._camera = camera
        self._clock = clock or time.monotonic
        self._run_logger = run_logger
        self._ids = itertools.count(1)
        self.task: Task | None = None

    def snapshot(self) -> dict[str, Any]:
        return self.task.to_dict() if self.task else {"state": "idle"}

    async def _begin(self, task: Task) -> Task:
        if self.task and self.task.state == "running":
            await self.cancel()
        task.started_at = self._clock()
        task._clock = self._clock
        self.task = task
        task._runner = asyncio.create_task(self._run(task))
        return task

    async def start(self, goal: str) -> Task:
        target = parse_find_goal(goal)
        if target and hasattr(self._brain, "turn_find"):
            return await self.start_find(target)   # voice and /plan goals like "find a chair" route to find mode
        self._brain.ensure_ready()  # BrainNotConfigured -> the router answers 503
        return await self._begin(Task(next(self._ids), goal))

    async def start_find(self, target: str) -> Task:
        raw = (target or "").strip().lower()
        if not valid_find_object(raw):
            raise ValueError("object must be 1-3 words of letters")
        target = raw
        if not hasattr(self._brain, "turn_find"):
            raise FindUnsupported("this brain has no find mode")
        self._brain.ensure_ready()
        return await self._begin(Task(next(self._ids), f"find {target}", mode="find", target=target))

    async def cancel(self, *, stop: bool = True) -> bool:
        """Cancel during a model call or step; optionally send a stop afterward."""
        task = self.task
        if task is None or task.state != "running":
            return False
        task.state = "cancelled"
        if task.mode == "find":
            task.outcome, task.message = "stopped_by_operator", "stopped by operator"
        runner = task._runner
        if runner is not None and not runner.done():
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        for step in task.steps:
            if step.state == "running":
                step.state = "skipped"
        if stop:
            await self._gateway.stop(source=SOURCE)
            if task.mode == "find":
                await self._reset_posture(task)   # level / medium again, after the stop
        logger.info("LLM task %s cancelled", task.id)
        return True

    def request_cancel(self) -> bool:
        """Synchronously mark and interrupt a task when the gateway preempts it.

        The preempting source (hand input, STOP, voice stop, e-stop) owns the robot from here: a stop of our own
        would fight a hand command, and an in-flight bounded move is already stopped by the gateway when it is
        cancelled. So no extra stop is sent on this path.
        """
        task = self.task
        if task is None or task.state != "running":
            return False
        task.state = "cancelled"
        if task.mode == "find":
            task.outcome, task.message = "cancelled", "interrupted by STOP, hand input or e-stop"
        for step in task.steps:
            if step.state == "running":
                step.state = "skipped"
        if task._runner is not None and not task._runner.done():
            task._runner.cancel()
        if task.mode == "find" and task.posture_dirty:
            asyncio.get_running_loop().create_task(self._reset_posture(task))   # best effort; the preempting source owns motion
        return True

    async def wait(self) -> None:
        if self.task and self.task._runner:
            await asyncio.gather(self.task._runner, return_exceptions=True)

    # ------------------------------------------------------------------ the loop

    async def _end(self, task: Task, state: str, message: str | None = None) -> None:
        task.state, task.message = state, message
        if task.mode == "find" and task.outcome is None:
            task.outcome = {"limit": "session_end" if (message or "").startswith("session limit")
                            else "timeout" if (message or "").startswith("time limit") else "limit",
                            "refused": "refused", "failed": "error"}.get(state, state)
        # A find run ALWAYS ends with a stop (found, timeout, camera lost, refusal, error). Plan runs only on failure.
        if state != "done" or task.mode == "find":
            await self._gateway.stop(source=SOURCE)
        if task.mode == "find":
            await self._reset_posture(task)

    async def _reset_posture(self, task: Task) -> None:
        """Level / medium again (best effort: a latched e-stop or an arbitration refusal is fine here)."""
        if not task.posture_dirty:
            return
        try:
            await self._gateway.set_posture("medium", 0.0, source=SOURCE)
            task.pitch, task.height = 0.0, "medium"
        except Exception:
            logger.warning("Could not reset the posture after the find run")

    async def _apply_posture(self, task: Task, height: str, pitch: float) -> None:
        """Set the body posture through the gateway and let it settle. ``pitch`` is the logical value the model used
        (positive = camera UP, already clamped to +/-10 deg); mapping it to the SDK's sign is the gateway's job (#135)."""
        limits = self._find_limits
        result = await self._gateway.set_posture(height, pitch, source=SOURCE)
        status = result.get("status") if isinstance(result, dict) else None
        if status in (None, "ACCEPTED", "STOP_DISPATCHED"):
            task.pitch, task.height = pitch, height
        await asyncio.sleep(limits.settle_s)

    def _log_run(self, task: Task) -> None:
        if self._run_logger is None or task.mode != "find":
            return
        try:
            self._run_logger(task.summary())
        except Exception:
            logger.exception("Could not log the LLM run")

    async def _run(self, task: Task) -> None:
        try:
            if task.mode == "find":
                await self._run_find(task)
            else:
                await self._run_loop(task)
        finally:
            task.ended_at = self._clock()
            self._log_run(task)

    async def _run_loop(self, task: Task) -> None:
        """The plain plan loop (not find mode)."""
        limits = self._limits
        deadline = self._clock() + limits.timeout_s
        task.deadline, task.timeout_s = deadline, limits.timeout_s

        def remaining() -> float:
            left = deadline - self._clock()
            if left <= 0:
                raise _Limit(f"time limit of {limits.timeout_s:g}s reached")
            return left

        try:
            messages = [initial_message(task.goal, await self._gateway.status(), await self._gateway.detections())]
            while True:
                if task.turns >= limits.max_turns:
                    raise _Limit(f"turn limit of {limits.max_turns} reached")
                remaining()
                task.turns += 1
                turn = await asyncio.wait_for(self._brain.turn(messages), remaining())
                self._count_usage(task, turn)

                if turn.stop_reason == "refusal":
                    return await self._end(task, "refused", "The assistant declined this request.")
                if turn.stop_reason == "max_tokens":
                    return await self._end(task, "failed", "model output was cut off")

                messages.append({"role": "assistant", "content": turn.content})  # verbatim, append-only
                if not turn.calls:
                    task.answer = turn.text
                    return await self._end(task, "done")

                results = []
                for index, call in enumerate(turn.calls):
                    if len(task.steps) >= limits.max_steps:
                        raise _Limit(f"step limit of {limits.max_steps} reached")
                    results.append(await self._run_call(task, call, remaining, note=turn.text if index == 0 else None))
                messages.append({"role": "user", "content": results})  # all results, one message, in order
        except _Limit as limit:
            await self._end(task, "limit", limit.message)
        except asyncio.TimeoutError:
            await self._end(task, "limit", f"time limit of {limits.timeout_s:g}s reached")
        except asyncio.CancelledError:
            raise  # cancel() owns the state change and the stop()
        except BrainError:
            await self._end(task, "failed", "model request failed")
        except Exception as exc:
            logger.warning("LLM task %s failed: %s", task.id, type(exc).__name__)
            await self._end(task, "failed", "a robot step failed")

    @staticmethod
    def _count_usage(task: Task, turn: Any) -> None:
        task.model_calls += 1
        if turn.usage:
            task.input_tokens += turn.usage.get("input_tokens", 0)
            task.output_tokens += turn.usage.get("output_tokens", 0)

    # ------------------------------------------------------------------ find session: search -> watch -> re-search

    async def _run_find(self, task: Task) -> None:
        """A find SESSION: search (Claude loop, <= 120 s) -> found -> WATCH (no motion, YOLO about once a second, no
        Claude call) -> target unseen for more than 2 s -> a new search, and so on, until the session cap
        (LLM_FIND_SESSION_S), a search that ends not-found, cancel/STOP/hand/e-stop. Every end sends a stop."""
        limits = self._find_limits
        session_end = self._clock() + limits.session_s
        task.session_deadline, task.session_s = session_end, limits.session_s
        try:
            while True:
                # After a loss the phase stays "lost" (the chip shows LOST -> re-search) until the new search's first step is done.
                task.phase = "lost" if task.phase == "lost" else "searching"
                task.found, task.answer, task.outcome = None, None, None
                task.searches += 1
                outcome = await self._search(task, session_end)
                if outcome != "found":   # the model gave up in plain text: not found
                    return await self._end(task, "done")
                task.phase = "watching"
                task.answer = f"Found {task.found['label']}: {task.found['where']}." if task.found["where"] else f"Found {task.found['label']}."
                await self._park(task)
                await self._watch(task, session_end)   # returns when the target is lost; raises _Limit at the session cap
                task.phase = "lost"                    # -> re-search for the same object
        except _Refused:
            task.outcome = None
            await self._end(task, "refused", "The assistant declined this request.")
        except _Limit as limit:
            task.outcome = None   # a stale "found" from an earlier search must not mask the session/search end
            await self._end(task, "limit", limit.message)
        except asyncio.TimeoutError:
            task.outcome = None
            await self._end(task, "limit", self._limit_message(session_end))
        except asyncio.CancelledError:
            raise  # cancel() owns the state change, the stop() and the posture reset
        except _CameraLost as lost:
            task.outcome = "camera_lost"
            await self._end(task, "failed", f"camera lost: {lost}")
        except BrainError:
            await self._end(task, "failed", "model request failed")
        except Exception as exc:
            logger.warning("LLM find task %s failed: %s", task.id, type(exc).__name__)
            await self._end(task, "failed", "a robot step failed")

    def _limit_message(self, session_end: float) -> str:
        if self._clock() >= session_end:
            return f"session limit of {self._find_limits.session_s:g}s reached"
        return f"time limit of {self._find_limits.timeout_s:g}s reached"

    async def _search(self, task: Task, session_end: float) -> str:
        """One search (<= 120 s and never past the session end). Returns "found", or "gave_up" (outcome not_found)."""
        limits = self._find_limits
        deadline = min(self._clock() + limits.timeout_s, session_end)
        task.deadline, task.timeout_s = deadline, limits.timeout_s
        step_base, turns = len(task.steps), 0
        messages = [find_initial_message(task.target or "", limits.timeout_s)]   # every search starts fresh

        def remaining() -> float:
            left = deadline - self._clock()
            if left <= 0:
                raise _Limit(self._limit_message(session_end))
            return left

        while True:
            if turns >= limits.max_turns:
                raise _Limit(f"turn limit of {limits.max_turns} reached")
            remaining()
            turns += 1
            task.turns += 1
            turn = await asyncio.wait_for(self._brain.turn_find(messages), remaining())
            self._count_usage(task, turn)
            if turn.stop_reason == "refusal":
                raise _Refused()
            if turn.stop_reason == "max_tokens":
                raise _Limit("model output was cut off")
            messages.append({"role": "assistant", "content": turn.content})  # verbatim, append-only
            if not turn.calls:
                task.outcome = "not_found"
                task.answer = turn.text or f"I could not find the {task.target}."
                return "gave_up"
            results = []
            for index, call in enumerate(turn.calls):
                if len(task.steps) - step_base >= limits.max_steps:
                    raise _Limit(f"step limit of {limits.max_steps} reached")
                results.append(await self._run_call(task, call, remaining, note=turn.text if index == 0 else None))
                if task._finish:   # found: do not run the rest of the batch
                    break
            if task._finish:
                label, where = task._finish
                task._finish = None
                task.found = {"label": label, "where": where}
                if getattr(task, "_last_look_jpeg", None):
                    import base64
                    task.found_snapshot = f"data:image/jpeg;base64,{base64.b64encode(task._last_look_jpeg).decode('ascii')}"
                task.outcome = "found"
                return "found"
            task.phase = "searching"
            messages.append({"role": "user", "content": results})

    async def _park(self, task: Task) -> None:
        """Found: stand still at level/medium and stay there while watching."""
        await self._gateway.stop(source=SOURCE)
        await self._reset_posture(task)

    async def _watch(self, task: Task, session_end: float) -> None:
        """No motion, no Claude call: poll the YOLO detections about once a second. Returns when the target has been
        unseen for more than ``lost_after_s``; raises _Limit when the session cap is reached."""
        limits = self._find_limits
        last_seen = self._clock()
        while True:
            if self._clock() >= session_end:
                raise _Limit(f"session limit of {limits.session_s:g}s reached")
            await asyncio.sleep(limits.watch_interval_s)
            try:
                raw = await self._gateway.detections()
            except Exception:
                raw = []
            if self._best_target(task, raw) is not None:
                last_seen = self._clock()
                if task.found is not None:
                    task.found["last_seen_s_ago"] = 0.0
            elif self._clock() - last_seen > limits.lost_after_s:
                return

    async def _run_call(self, task: Task, call: Any, remaining, note: str | None = None) -> dict[str, Any]:
        step = Step(call.name, call.input, note=(note or "").strip()[:160] or None)
        task.steps.append(step)
        block = {"type": "tool_result", "tool_use_id": call.id}
        try:
            validated = validate_call(call.name, call.input, task.mode)
        except PlanValidationError as exc:  # never executed; the model gets to correct it
            step.state, step.error = "rejected", str(exc)
            return {**block, "is_error": True, "content": f"Invalid call, not executed: {exc}"}
        if task.mode == "find":
            problem = None
            if validated.name in ("move", "turn") and not task.looked:
                problem = "call look first: you must look before your first move or turn and between any two of them"
            elif validated.name == "found" and task.looks == 0:
                problem = "you have not looked yet: call look and only report an object you can see"
            if problem:
                step.state, step.error = "rejected", problem
                return {**block, "is_error": True, "content": f"Not executed: {problem}."}
        try:
            if task.mode == "find" and validated.name == "move" and task.posture_dirty:
                await self._reset_posture(task)      # never walk pitched or crouched: level / medium first (executor-enforced)
                await asyncio.sleep(self._find_limits.settle_s)
            if validated.name == "look":
                return await asyncio.wait_for(self._run_look(task, step, block, validated.args), remaining())
            step.result = await asyncio.wait_for(self._dispatch(validated, task), remaining())
        except (asyncio.TimeoutError, asyncio.CancelledError):
            step.state = "skipped"
            raise
        except CameraLost as lost:
            step.state, step.error = "failed", str(lost)
            raise _CameraLost(str(lost))
        except Exception:
            step.state = "failed"
            raise
        step.state = "done"
        if task.mode == "find" and validated.name in ("move", "turn"):
            task.looked = False   # the next motion needs a fresh look
        return {**block, "content": _result_text(step.result)}

    async def _run_look(self, task: Task, step: Step, block: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        # Vertical view: pitch is already clamped to +/-10 deg by validate_call; None = level. Height None = keep.
        want_pitch = float(args.get("pitch_deg") or 0.0)
        want_height = args.get("height") or task.height
        if (want_pitch, want_height) != (task.pitch, task.height):
            await self._apply_posture(task, want_height, want_pitch)   # then settle ~0.5 s before the photo
        image = await capture_look(self._camera, clock=self._clock)   # CameraLost -> the run stops
        task._last_look_jpeg = image.jpeg
        try:
            raw = await self._gateway.detections()
        except Exception:
            raw = []
        detections = [{"label": d.get("label"), "confidence": d.get("confidence"), "box": d.get("box"),
                       "age_s": d.get("age_s")} for d in raw if isinstance(d, dict)]
        summary = {"frame_age_s": image.frame_age_s, "image_size": [image.width, image.height],
                   "posture": {"pitch_deg": task.pitch, "height": task.height},   # what was actually used
                   "detections": detections}
        task.looked = True
        task.looks += 1
        step.args = {"pitch_deg": want_pitch, "height": want_height}
        step.result = {**summary, "image": f"{len(image.jpeg)} bytes sent to the model"}   # never the pixels
        step.state = "done"
        self._check_target(task, raw)
        # The image block goes INSIDE the tool_result's content list, next to the text.
        return {**block, "content": [image.block(), {"type": "text", "text": json.dumps(summary, default=str)}]}

    def _best_target(self, task: Task, detections: list[Any]) -> dict[str, Any] | None:
        """The most confident detection of the target at >= the confidence threshold, or None."""
        wanted = target_labels(task.target or "")
        best = None
        for d in detections:
            if not isinstance(d, dict) or str(d.get("label", "")).lower() not in wanted:
                continue
            conf = d.get("confidence")
            if isinstance(conf, (int, float)) and conf >= self._find_limits.min_confidence and (
                    best is None or conf > best.get("confidence", 0)):
                best = d
        return best

    def _check_target(self, task: Task, detections: list[Any]) -> None:
        """Auto-finish the moment YOLO reports the target with enough confidence, so it doesn't overshoot."""
        best = self._best_target(task, detections)
        if best is not None:
            task._finish = (str(best.get("label")), describe_where(best))

    async def _dispatch(self, call: ToolCall, task: Task | None = None) -> Any:
        g, a = self._gateway, call.args
        if call.name == "move":
            return await g.run_bounded(a["direction"], a["seconds"], a["speed"], source=SOURCE)
        if call.name == "turn":
            return await g.turn(a["degrees"], source=SOURCE)
        if call.name == "stop":
            return await g.stop(source=SOURCE)
        if call.name == "found":
            if task is not None:
                task._finish = (a["label"], a["where"])
            return {"ok": True, "message": "Reported. The run ends and the robot stops."}
        if call.name == "set_style":
            return await g.set_style(a["intensity"], a["gait"], source=SOURCE)
        if call.name == "run_action":
            return await g.run_action(a["action_id"], source=SOURCE)
        if call.name == "get_status":
            return await g.status()
        if call.name == "get_detections":
            return await g.detections()
        raise ValueError(f"unknown tool {call.name}")  # unreachable: calls are validated
