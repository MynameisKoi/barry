"""Goal -> plan. One Messages call returns the ordered tool calls, which are then validated.

Claude API choices for ``claude-sonnet-5-5`` (see the PR description):
* ``tool_choice`` stays ``auto``: forced ``any``/``tool`` returns a 400 on this model. A system
  prompt says to answer with tool calls, and the tools are ``strict``.
* Latency: adaptive thinking (the only on-mode) at ``effort: "low"``. Plans are a few short
  moves, so deep reasoning buys little, and low effort skips thinking on simple goals.
* Server-side refusal fallback is on (``fallbacks: "default"``); a refusal that survives it is
  returned as ``status="refused"`` and never executed.
* max_tokens=8000 is only a ceiling (thinking tokens count against it); it does not add latency.
* One call, no tool-result loop: the tool calls ARE the plan. Parallel tool use keeps their order.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.ai.control.tools import (
    TOOL_DEFS,
    PlanValidationError,
    ToolCall,
    validate_plan,
)

logger = logging.getLogger(__name__)

ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV = "ANTHROPIC_CONTROL_MODEL"
DEFAULT_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You control a DOGZILLA quadruped robot. The operator gives a short goal.
Answer a request to move or look by calling the robot tools, in order, in a single response.
Do not narrate; do not ask for confirmation, the plan runs immediately and the operator can stop it.
Use only the tools provided and keep every value inside the ranges they state. If the goal needs
more than the limits allow (for example a move longer than the maximum), split it into several
move calls. If the operator only asks a question or chats, answer in plain text and call no tools.
You are given the robot's current status and detections as context."""


class PlannerNotConfigured(RuntimeError):
    """ANTHROPIC_API_KEY is not set."""


class PlannerError(RuntimeError):
    """The planner call failed or returned something unusable."""


@dataclass(frozen=True)
class PlanResult:
    status: str  # "plan" | "answer" | "refused"
    plan: list[ToolCall] = field(default_factory=list)
    text: str = ""


class Planner(Protocol):
    async def plan(self, goal: str, status: dict[str, Any], detections: list[Any]) -> PlanResult: ...


def _context(goal: str, status: dict[str, Any], detections: list[Any]) -> str:
    return f"Robot status: {status}\nLatest detections: {detections}\nOperator goal: {goal}"


class AnthropicPlanner:
    def __init__(self, client=None, model: str | None = None) -> None:
        self._client = client
        self._model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL

    def _get_client(self):
        if self._client is None:
            key = os.environ.get(ANTHROPIC_KEY_ENV, "").strip()
            if not key:
                raise PlannerNotConfigured("ANTHROPIC_API_KEY is not set")
            from anthropic import AsyncAnthropic  # lazy: the app boots without a key

            self._client = AsyncAnthropic(api_key=key)
        return self._client

    async def plan(self, goal: str, status: dict[str, Any], detections: list[Any]) -> PlanResult:
        client = self._get_client()
        try:
            response = await client.beta.messages.create(
                model=self._model,
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFS,
                tool_choice={"type": "auto"},
                output_config={"effort": "low"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
                messages=[{"role": "user", "content": _context(goal, status, detections)}],
            )
        except Exception as exc:  # never include the key or request body
            logger.warning("Planner request failed: %s", type(exc).__name__)
            raise PlannerError("planner request failed") from exc

        if response.stop_reason == "refusal":
            logger.warning("Planner refused (category=%s)", getattr(response.stop_details, "category", None))
            return PlanResult("refused", text="The assistant declined this request.")
        if response.stop_reason == "max_tokens":
            raise PlannerError("planner output was cut off")

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        calls = [(b.name, b.input) for b in response.content if b.type == "tool_use"]
        if not calls:
            return PlanResult("answer", text=text)
        return PlanResult("plan", plan=validate_plan(calls), text=text)  # may raise PlanValidationError


# ---------------------------------------------------------------- deterministic mock planner

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


class MockPlanner:
    """Keyword planner for tests and offline demos. Deliberately does NOT clamp numbers, so an
    out-of-range request ("forward 50 seconds") produces an out-of-range call that validation
    must reject, exactly as it would for a misbehaving model."""

    async def plan(self, goal: str, status: dict[str, Any], detections: list[Any]) -> PlanResult:
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
            found.append((len(text), "get_status", {}))
        if not found:
            return PlanResult("answer", text="I can move, turn, stop, and report what I see.")
        found.sort(key=lambda f: f[0])
        return PlanResult("plan", plan=validate_plan([(n, a) for _, n, a in found]))


def create_planner() -> Planner:
    """``LLM_PLANNER=mock`` selects the offline planner; otherwise Claude."""
    if os.environ.get("LLM_PLANNER", "").lower() == "mock":
        return MockPlanner()
    return AnthropicPlanner()


__all__ = ["AnthropicPlanner", "MockPlanner", "PlanResult", "Planner", "PlannerError",
           "PlannerNotConfigured", "PlanValidationError", "create_planner"]
