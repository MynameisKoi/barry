"""Robot tool schemas for the LLM brain, and the validator for every call it makes.

The schemas are derived from the Phase 0 robot contract (``backend/robot``). They are sent to
the model with ``strict: true`` so arguments arrive schema-shaped, but numeric ranges are NOT
expressed as JSON-Schema ``minimum``/``maximum`` (strict mode does not take them). The ranges are
stated in the descriptions and enforced here by :func:`validate_call`: an invalid call is never
executed (the loop returns it to the model as an ``is_error`` tool result), and the gateway (#57)
applies its own limits again on top.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from backend.robot.config import APPROVED_ACTIONS
from backend.robot.interface import Gait, Intensity

DIRECTIONS = ("forward", "backward", "left", "right")
MIN_SECONDS, MAX_SECONDS = 0.2, 5.0
MAX_SPEED = 0.6  # ROBOT_MAX_SPEED in the build plan
MAX_DEGREES = 180.0
INTENSITIES = tuple(i.value for i in Intensity)
GAITS = tuple(g.value for g in Gait)
ACTION_IDS = tuple(sorted(APPROVED_ACTIONS))

READ_ONLY_TOOLS = frozenset({"get_status", "get_detections"})


class PlanValidationError(ValueError):
    """A tool call is malformed or out of range; it is not executed."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


def _tool(name: str, description: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    properties = properties or {}
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOL_DEFS: list[dict[str, Any]] = [
    _tool(
        "move",
        f"Walk in a direction for a bounded time. seconds must be {MIN_SECONDS}-{MAX_SECONDS}; "
        f"speed is 0-{MAX_SPEED} (use 0.3 if the user gave none).",
        {
            "direction": {"type": "string", "enum": list(DIRECTIONS)},
            "seconds": {"type": "number", "description": f"{MIN_SECONDS}-{MAX_SECONDS}"},
            "speed": {"type": "number", "description": f"0-{MAX_SPEED}"},
        },
    ),
    _tool(
        "turn",
        f"Turn in place by degrees: positive = right, negative = left, magnitude at most {MAX_DEGREES:g}.",
        {"degrees": {"type": "number", "description": f"-{MAX_DEGREES:g} to {MAX_DEGREES:g}"}},
    ),
    _tool("stop", "Stop all motion immediately."),
    _tool(
        "set_style",
        "Set movement intensity and gait for later moves.",
        {
            "intensity": {"type": "string", "enum": list(INTENSITIES)},
            "gait": {"type": "string", "enum": list(GAITS)},
        },
    ),
    _tool(
        "run_action",
        "Run one approved preset action by id: "
        + ", ".join(f"{i}={n}" for i, n in sorted(APPROVED_ACTIONS.items()))
        + ". No other ids exist.",
        {"action_id": {"type": "integer", "enum": list(ACTION_IDS)}},
    ),
    _tool("get_status", "Read the robot's current status (read-only)."),
    _tool(
        "get_detections",
        "Run object detection on the robot camera's newest frame (read-only). Returns a list of "
        "{label, confidence, box, age_s}: age_s is how many seconds old the frame is. The list is empty "
        "when the camera or detector is unavailable or the frame is stale, so an empty list does not "
        "prove nothing is there."),
]
_TOOL_NAMES = {t["name"] for t in TOOL_DEFS}


def _number(value: Any, what: str) -> float:
    # bool is an int subclass; True is not a number here.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlanValidationError(f"{what} must be a finite number")
    return float(value)


def _enum(value: Any, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise PlanValidationError(f"{what} must be one of {list(allowed)}")
    return value


def validate_call(name: Any, args: Any) -> ToolCall:
    """Validate one tool call; returns a normalised :class:`ToolCall` or raises."""
    if name not in _TOOL_NAMES:
        raise PlanValidationError(f"unknown tool {name!r}")
    if not isinstance(args, dict):
        raise PlanValidationError(f"{name}: arguments must be an object")
    schema_props = next(t for t in TOOL_DEFS if t["name"] == name)["input_schema"]["properties"]
    if set(args) != set(schema_props):
        raise PlanValidationError(f"{name}: expected arguments {sorted(schema_props)}, got {sorted(args)}")

    if name == "move":
        seconds = _number(args["seconds"], "move.seconds")
        speed = _number(args["speed"], "move.speed")
        if not MIN_SECONDS <= seconds <= MAX_SECONDS:
            raise PlanValidationError(f"move.seconds must be {MIN_SECONDS}-{MAX_SECONDS}")
        if not 0 <= speed <= MAX_SPEED:
            raise PlanValidationError(f"move.speed must be 0-{MAX_SPEED}")
        return ToolCall(name, {"direction": _enum(args["direction"], DIRECTIONS, "move.direction"),
                               "seconds": seconds, "speed": speed})
    if name == "turn":
        degrees = _number(args["degrees"], "turn.degrees")
        if not -MAX_DEGREES <= degrees <= MAX_DEGREES:
            raise PlanValidationError(f"turn.degrees must be -{MAX_DEGREES:g} to {MAX_DEGREES:g}")
        return ToolCall(name, {"degrees": degrees})
    if name == "set_style":
        return ToolCall(name, {"intensity": _enum(args["intensity"], INTENSITIES, "set_style.intensity"),
                               "gait": _enum(args["gait"], GAITS, "set_style.gait")})
    if name == "run_action":
        action_id = args["action_id"]
        if isinstance(action_id, bool) or not isinstance(action_id, int) or action_id not in APPROVED_ACTIONS:
            raise PlanValidationError(f"run_action.action_id must be one of {list(ACTION_IDS)}")
        return ToolCall(name, {"action_id": action_id})
    return ToolCall(name, {})  # stop, get_status, get_detections
