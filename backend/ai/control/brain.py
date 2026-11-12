"""The model side of the brain loop: one Messages call per turn, plus an offline mock.

Claude API choices for ``claude-sonnet-5-5`` (see the PR description):
* ``tool_choice`` stays ``auto``: forced ``any``/``tool`` returns a 400 on this model. The system
  prompt says to act through tool calls and answer in text when done; the tools are ``strict``.
* Latency: adaptive thinking (the only on-mode) at ``effort: "low"``. ``max_tokens=8000`` is only a
  ceiling (thinking tokens count against it) and adds no latency.
* Server-side refusal fallback is on (``fallbacks: "default"``); a refusal that survives it ends
  the task with state ``refused``.
* Append-only history: the loop appends ``response.content`` verbatim (thinking blocks included)
  and never edits an earlier turn, which keeps preserved thinking valid.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.ai.control.tools import FIND_TOOL_DEFS, TOOL_DEFS

logger = logging.getLogger(__name__)

ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV = "ANTHROPIC_CONTROL_MODEL"
DEFAULT_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are the brain of a DOGZILLA quadruped robot. The operator gives you a goal.
You act by calling the robot tools and you see each tool's result before you choose the next step.
Take one step at a time when a result will decide what to do next (for example call get_detections,
read the result, then answer or move). Calls you make in one response run in order. Keep every value
inside the ranges the tools state; if a call comes back as an error, read it and correct the call.
The robot moves immediately and the operator can stop it at any time, so do not ask for
confirmation. When the goal is done, or if the operator only asks a question, reply in plain text
with a short answer that uses what the tools returned. You are given the robot's status and
detections at the start as context.
Detections come from the robot's own camera (a COCO object detector) and each carries age_s, the age of
the frame in seconds. They may be empty when the camera or detector is unavailable, which is not
evidence that nothing is there: say you could not see rather than guessing. Only mention objects that
a detection result actually listed; never invent or assume objects, and mention age_s if it is large."""


FIND_SYSTEM_PROMPT = """You are the brain of a DOGZILLA quadruped robot, in FIND mode. The operator names an object and you
must find it with the robot's camera, within a strict time budget (the run ends by itself).
You see only through the look tool: each call returns a photo from the robot's own camera plus the detections of a
COCO object detector for the same moment (label, confidence, box, age_s). A detector can miss things or be wrong:
trust what you can actually see in the photo.
Strategy: look; if the target is visible and close enough, call found(label, where). If not, scan the way a person
looks around: at each heading look level, then look down (pitch_deg -10, to catch things right in front of the robot or
on the floor), then look up (pitch_deg 10, for things higher up), and only then turn in a step of at most 45 degrees. If a
whole circle finds nothing, look again with height "low". Otherwise choose ONE small action: step forward at most
1 second at speed 0.3 or less toward where it is likely to be. The body is levelled automatically before any step.
Then look again. You may never move or turn without
having looked since your last motion: such a call is rejected and wastes time. Before every tool call write one short
sentence saying what you see and why you are acting. Never claim to see something that is not in the photo, and
never call found unless you can see the target. If after a full scan and some exploring you cannot find it, say so in
plain text (that ends the run as not found). The robot is stopped automatically when the run ends, and the operator
can cancel at any time."""


class BrainNotConfigured(RuntimeError):
    """ANTHROPIC_API_KEY is not set."""


class BrainError(RuntimeError):
    """The model call failed."""


@dataclass(frozen=True)
class RawCall:
    id: str
    name: str
    input: Any


@dataclass
class ModelTurn:
    """One model response, provider-neutral. ``content`` goes into the history verbatim."""

    stop_reason: str  # "end_turn" | "tool_use" | "refusal" | "max_tokens"
    text: str = ""
    calls: list[RawCall] = field(default_factory=list)
    content: Any = None
    usage: dict[str, int] | None = None  # input/output tokens of this call, when the provider reports them


class Brain(Protocol):
    def ensure_ready(self) -> None: ...
    async def turn(self, messages: list[dict[str, Any]]) -> ModelTurn: ...


def find_initial_message(target: str, timeout_s: float) -> dict[str, Any]:
    return {"role": "user",
            "content": f"Find: {target}\nYou have {timeout_s:g} seconds. Start by calling look."}


def initial_message(goal: str, status: dict[str, Any], detections: list[Any]) -> dict[str, Any]:
    return {"role": "user",
            "content": f"Robot status: {status}\nLatest detections: {detections}\nOperator goal: {goal}"}


class AnthropicBrain:
    def __init__(self, client=None, model: str | None = None) -> None:
        self._client = client
        self._model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL

    def ensure_ready(self) -> None:
        self._get_client()

    def _get_client(self):
        if self._client is None:
            key = os.environ.get(ANTHROPIC_KEY_ENV, "").strip()
            if not key:
                raise BrainNotConfigured("ANTHROPIC_API_KEY is not set")
            from anthropic import AsyncAnthropic  # lazy: the app boots without a key

            self._client = AsyncAnthropic(api_key=key)
        return self._client

    async def turn(self, messages: list[dict[str, Any]]) -> ModelTurn:
        return await self._call(messages, SYSTEM_PROMPT, TOOL_DEFS)

    async def turn_find(self, messages: list[dict[str, Any]]) -> ModelTurn:
        """One turn of an autonomous find run: find-mode prompt and tool set. The look() results in the
        history carry image blocks (appended verbatim, never edited, so earlier turns stay valid)."""
        return await self._call(messages, FIND_SYSTEM_PROMPT, FIND_TOOL_DEFS)

    async def _call(self, messages: list[dict[str, Any]], system: str, tools: list[dict[str, Any]]) -> ModelTurn:
        client = self._get_client()
        try:
            response = await client.beta.messages.create(
                model=self._model,
                max_tokens=8000,
                system=system,
                tools=tools,
                tool_choice={"type": "auto"},
                output_config={"effort": "low"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
                messages=messages,
            )
        except Exception as exc:  # never include the key or request body
            logger.warning("Model request failed: %s", type(exc).__name__)
            raise BrainError("model request failed") from exc

        if response.stop_reason == "refusal":
            logger.warning("Model refused (category=%s)", getattr(response.stop_details, "category", None))
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        calls = [RawCall(b.id, b.name, b.input) for b in response.content if b.type == "tool_use"]
        stop = response.stop_reason if response.stop_reason in ("refusal", "max_tokens") else (
            "tool_use" if calls else "end_turn")
        usage = getattr(response, "usage", None)
        counts = None
        if usage is not None:
            counts = {"input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                      "output_tokens": int(getattr(usage, "output_tokens", 0) or 0)}
        return ModelTurn(stop, text, calls, response.content, usage=counts)


# ---------------------------------------------------------------- deterministic mock brain

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10,
          "half": 0.5, "ninety": 90, "forty five": 45}
_NUM = r"(\d+(?:\.\d+)?|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"
_MOVE = re.compile(rf"\b(forward|back(?:ward)?s?)\b(?:\s+(?:for\s+)?{_NUM}\s*(?:seconds?|secs?|s)\b)?")
_TURN = re.compile(rf"\bturn\s+(left|right)\b(?:\s+(?:by\s+)?{_NUM}\s*(?:degrees?|deg)\b)?")
_STOP = re.compile(r"\bstop\b")
_LOOK = re.compile(r"\b(see|look|detect\w*|report)\b")
_STATUS = re.compile(r"\b(status|battery)\b")


def _num(token: str | None, default: float) -> float:
    if token is None:
        return default
    return float(_WORDS[token]) if token in _WORDS else float(token)


def parse_goal(goal: str) -> list[tuple[str, dict[str, Any]]]:
    """Keyword goal -> ordered (tool, args). Deliberately unclamped, so an out-of-range goal
    ("forward 50 seconds") yields a call the loop must reject, like a misbehaving model."""
    text = " ".join(goal.lower().split())
    found: list[tuple[int, str, dict[str, Any]]] = []
    for m in _MOVE.finditer(text):
        direction = "forward" if m.group(1) == "forward" else "backward"
        found.append((m.start(), "move", {"direction": direction, "seconds": _num(m.group(2), 1.0), "speed": 0.3}))
    for m in _TURN.finditer(text):
        sign = 1.0 if m.group(1) == "right" else -1.0
        found.append((m.start(), "turn", {"degrees": sign * _num(m.group(2), 90.0)}))
    for m in _STOP.finditer(text):
        found.append((m.start(), "stop", {}))
    if _LOOK.search(text):
        found.append((len(text), "get_detections", {}))
    if _STATUS.search(text):
        found.append((len(text) + 1, "get_status", {}))
    found.sort(key=lambda f: f[0])
    return [(n, a) for _, n, a in found]


def _clamp_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    from backend.ai.control import tools

    fixed = dict(args)
    if name == "move":
        fixed["seconds"] = min(max(fixed["seconds"], tools.MIN_SECONDS), tools.MAX_SECONDS)
        fixed["speed"] = min(max(fixed["speed"], 0.0), tools.MAX_SPEED)
    if name == "turn":
        fixed["degrees"] = min(max(fixed["degrees"], -tools.MAX_DEGREES), tools.MAX_DEGREES)
    return fixed


def _labels(result_text: str) -> list[str]:
    try:
        data = json.loads(result_text)
    except ValueError:
        return []
    items = data if isinstance(data, list) else []
    return [str(i.get("label") or i.get("class") or i) if isinstance(i, dict) else str(i) for i in items]


class MockBrain:
    """Deterministic fake model (``LLM_PLANNER=mock``) that exercises the loop offline: it issues the
    calls its keyword parser finds, corrects an out-of-range call once after an ``is_error`` result,
    and finishes with text that uses the ``get_detections`` result."""

    def ensure_ready(self) -> None:
        return None

    async def turn_find(self, messages: list[dict[str, Any]]) -> ModelTurn:
        return MockFindBrain().turn_find_sync(messages)

    async def turn(self, messages: list[dict[str, Any]]) -> ModelTurn:
        def blocks(content: Any) -> list[Any]:
            return content if isinstance(content, list) else []

        assistant_turns = sum(1 for m in messages if m["role"] == "assistant")
        last = messages[-1]
        results = [b for b in blocks(last["content"]) if isinstance(b, dict) and b.get("type") == "tool_result"]

        if not results:  # first turn: read the goal
            goal = messages[0]["content"].split("Operator goal:", 1)[-1]
            calls = parse_goal(goal)
            if not calls:
                text = "I can move, turn, stop, and tell you what I see."
                return ModelTurn("end_turn", text, [], [{"type": "text", "text": text}])
            return self._call_turn(calls)

        errors = [b for b in results if b.get("is_error")]
        if errors and assistant_turns <= 1:  # correct the rejected calls once
            prev = [b for b in blocks(messages[-2]["content"]) if b.get("type") == "tool_use"]
            bad_ids = {b["tool_use_id"] for b in errors}
            fixed = [(b["name"], _clamp_call(b["name"], b["input"])) for b in prev if b["id"] in bad_ids]
            return self._call_turn(fixed)

        # Use get_detections results from ANY earlier turn, not just the last one.
        detection_ids = {b["id"] for m in messages if m["role"] == "assistant"
                         for b in blocks(m["content"])
                         if isinstance(b, dict) and b.get("type") == "tool_use" and b["name"] == "get_detections"}
        seen: list[str] = []
        for m in messages:
            for b in blocks(m["content"]) if m["role"] == "user" else []:
                if (isinstance(b, dict) and b.get("type") == "tool_result" and not b.get("is_error")
                        and b.get("tool_use_id") in detection_ids and isinstance(b["content"], str)):
                    seen += _labels(b["content"])
        text = ("I see: " + ", ".join(seen) + ".") if seen else "Done."
        return ModelTurn("end_turn", text, [], [{"type": "text", "text": text}])

    @staticmethod
    def _call_turn(calls: list[tuple[str, dict[str, Any]]]) -> ModelTurn:
        raw = [RawCall(f"mock_{i}", n, a) for i, (n, a) in enumerate(calls)]
        content = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in raw]
        return ModelTurn("tool_use", "", raw, content)


def _result_text(content: Any) -> str:
    """The text of a tool_result's content (a string, or a list of text/image blocks)."""
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _mock_labels(target: str) -> set[str]:
    from backend.ai.control.executor import target_labels   # local import: executor imports this module

    return target_labels(target)


def _find_target(messages: list[dict[str, Any]]) -> str:
    first = messages[0]["content"]
    first = first if isinstance(first, str) else _result_text(first)
    return first.split("\n", 1)[0].removeprefix("Find:").strip().lower()


class MockFindBrain:
    """Deterministic fake for the find loop (offline demos, tests): look -> turn -> look -> step -> look ...
    then found() when the target appears in a look() result, or a plain-text "not found" after MAX_LOOKS."""

    # Scan pattern per heading: look level, look down, look up, then turn 45 deg. After a full circle without a hit, try the
    # low body height. 'MAX_LOOKS' caps the run for tests/demos.
    MAX_LOOKS = 40
    PATTERN = ({}, {"pitch_deg": -10.0}, {"pitch_deg": 10.0})

    def turn_find_sync(self, messages: list[dict[str, Any]]) -> ModelTurn:
        target = _find_target(messages)
        looks = sum(1 for m in messages if m["role"] == "assistant" and isinstance(m["content"], list)
                    for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "look")
        last = messages[-1]
        seen: list[dict[str, Any]] = []
        if last["role"] == "user" and isinstance(last["content"], list):
            for block in last["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result" and not block.get("is_error"):
                    try:
                        seen = json.loads(_result_text(block["content"])).get("detections", [])
                    except ValueError:
                        seen = []
        hit = next((d for d in seen if str(d.get("label", "")).lower() in _mock_labels(target)
                    and float(d.get("confidence") or 0) >= 0.5), None)
        n = len([m for m in messages if m["role"] == "assistant"])
        uid = len(messages)
        last_tool = self._last_tool(messages)
        if hit is not None:
            return self._call("found", {"label": target, "where": "ahead"}, f"I can see the {target}.", uid)
        if looks >= self.MAX_LOOKS:
            text = f"I could not find the {target}."
            return ModelTurn("end_turn", text, [], [{"type": "text", "text": text}])
        if n == 0 or last_tool == "turn" or last_tool == "move":
            return self._call("look", {}, f"Looking for the {target}, level.", uid)
        # last tool was a look: continue the vertical scan at this heading, or turn to the next heading
        since = self._trailing_looks(messages)               # looks taken at the current heading: 1 -> down, 2 -> up, 3 -> turn on
        if since in (1, 2):
            args = {"pitch_deg": self.PATTERN[since].get("pitch_deg"), "height": None}
            return self._call("look", args, "Nothing yet; tilting to " + ("look down." if since == 1 else "look up."), uid)
        turns = sum(1 for m in messages if m["role"] == "assistant" and isinstance(m["content"], list)
                    for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "turn")
        tried_low = any(isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "look"
                        and (b.get("input") or {}).get("height") == "low"
                        for m in messages if m["role"] == "assistant" and isinstance(m["content"], list) for b in m["content"])
        if turns >= 8 and not tried_low:                     # a full 360 deg of 45 deg turns without a hit
            return self._call("look", {"pitch_deg": None, "height": "low"}, "Nothing around; trying the low body height.", uid)
        return self._call("turn", {"degrees": 45}, "Not here; turning to the next heading.", uid)

    @staticmethod
    def _trailing_looks(messages: list[dict[str, Any]]) -> int:
        count = 0
        for m in reversed(messages):
            if m["role"] != "assistant" or not isinstance(m["content"], list):
                continue
            names = [b.get("name") for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
            if not names or names[-1] != "look":
                break
            count += 1
        return count

    @staticmethod
    def _last_tool(messages: list[dict[str, Any]]) -> str | None:
        for m in reversed(messages):
            if m["role"] == "assistant" and isinstance(m["content"], list):
                names = [b.get("name") for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
                return names[-1] if names else None
        return None

    @staticmethod
    def _call(name: str, args: dict[str, Any], note: str, uid: int) -> ModelTurn:
        call = RawCall(f"mock_{uid}", name, args)
        content = [{"type": "text", "text": note},
                   {"type": "tool_use", "id": call.id, "name": name, "input": args}]
        return ModelTurn("tool_use", note, [call], content)


def create_brain() -> Brain:
    """``LLM_PLANNER=mock`` selects the offline brain; otherwise Claude."""
    if os.environ.get("LLM_PLANNER", "").lower() == "mock":
        return MockBrain()
    return AnthropicBrain()
