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

from backend.robot.config import APPROVED_ACTIONS, ATTITUDE_SAFE_LIMITS
from backend.robot.interface import Gait, Intensity

DIRECTIONS = ("forward", "backward", "left", "right")
MIN_SECONDS, MAX_SECONDS = 0.2, 5.0
MAX_SPEED = 0.6  # ROBOT_MAX_SPEED in the build plan
MAX_DEGREES = 180.0
INTENSITIES = tuple(i.value for i in Intensity)
GAITS = tuple(g.value for g in Gait)
ACTION_IDS = tuple(sorted(APPROVED_ACTIONS))

READ_ONLY_TOOLS = frozenset({"get_status", "get_detections", "look"})

# Autonomous "find an object" mode (Phase 6): much tighter limits, enforced here and in the executor.
FIND_MAX_SECONDS = 1.0
FIND_MAX_SPEED = 0.3
FIND_MAX_DEGREES = 45.0
FIND_MAX_PITCH = float(ATTITUDE_SAFE_LIMITS["pitch"])   # look() never tilts the body further than this (10 deg)
HEIGHTS = ("low", "medium", "high")
FIND_LABEL_MAX = 40
FIND_WHERE_MAX = 120


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

# The find-mode tool set: look first, then small bounded moves, then report. No styles, actions or raw reads.
FIND_TOOL_DEFS: list[dict[str, Any]] = [
    _tool(
        "look",
        "Take a photo with the robot's camera. Returns the image plus the YOLO detections for the same moment "
        "({label, confidence, box [ymin,xmin,ymax,xmax] 0-1, age_s}), the frame age and the posture actually used. "
        "Optional vertical view: pitch_deg tilts the body, positive = camera UP, negative = camera DOWN, clamped to "
        f"+/-{FIND_MAX_PITCH:g} degrees (null = level); height is the body height low/medium/high (null = keep the "
        "current one; low lets you see things right in front of the robot). The body settles for about half a second "
        "first. Posture is reset to level/medium automatically before any move and when the run ends. You must call "
        "look before your first move or turn, and again between any two moves or turns.",
        {
            "pitch_deg": {"type": ["number", "null"], "description": f"-{FIND_MAX_PITCH:g} to {FIND_MAX_PITCH:g} or null"},
            "height": {"type": ["string", "null"], "enum": [*HEIGHTS, None], "description": "low | medium | high | null"},
        },
    ),
    _tool(
        "move",
        f"Take one small step in a direction. seconds must be {MIN_SECONDS}-{FIND_MAX_SECONDS:g}; speed 0-{FIND_MAX_SPEED:g}. "
        "Larger values are rejected. Look afterwards.",
        {
            "direction": {"type": "string", "enum": list(DIRECTIONS)},
            "seconds": {"type": "number", "description": f"{MIN_SECONDS}-{FIND_MAX_SECONDS:g}"},
            "speed": {"type": "number", "description": f"0-{FIND_MAX_SPEED:g}"},
        },
    ),
    _tool(
        "turn",
        f"Turn in place by degrees: positive = right, negative = left, at most {FIND_MAX_DEGREES:g} either way. Look afterwards.",
        {"degrees": {"type": "number", "description": f"-{FIND_MAX_DEGREES:g} to {FIND_MAX_DEGREES:g}"}},
    ),
    _tool(
        "found",
        "Call this when you can see the target object in the latest photo and it is close enough. Ends the run; "
        "the robot stops. Do not call it unless you actually see the object.",
        {
            "label": {"type": "string", "description": "what you found"},
            "where": {"type": "string", "description": "short description of where it is, e.g. 'ahead, about 1 m'"},
        },
    ),
    _tool("stop", "Stop all motion immediately."),
]
_FIND_TOOL_NAMES = {t["name"] for t in FIND_TOOL_DEFS}


def _number(value: Any, what: str) -> float:
    # bool is an int subclass; True is not a number here.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlanValidationError(f"{what} must be a finite number")
    return float(value)


def _enum(value: Any, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise PlanValidationError(f"{what} must be one of {list(allowed)}")
    return value


def validate_call(name: Any, args: Any, mode: str = "plan") -> ToolCall:
    """Validate one tool call; returns a normalised :class:`ToolCall` or raises.

    ``mode="find"`` uses the find tool set and its tight limits (move <= 1 s at <= 0.3, turn <= 45 deg).
    """
    find = mode == "find"
    defs = FIND_TOOL_DEFS if find else TOOL_DEFS
    if name not in (_FIND_TOOL_NAMES if find else _TOOL_NAMES):
        raise PlanValidationError(f"unknown tool {name!r}")
    if not isinstance(args, dict):
        raise PlanValidationError(f"{name}: arguments must be an object")
    schema_props = next(t for t in defs if t["name"] == name)["input_schema"]["properties"]
    if name == "look":
        # Both arguments are optional (null or absent = defaults); anything else is rejected.
        if not set(args) <= set(schema_props):
            raise PlanValidationError(f"look: unexpected arguments {sorted(set(args) - set(schema_props))}")
        pitch, height = args.get("pitch_deg"), args.get("height")
        if pitch is not None:
            pitch = max(-FIND_MAX_PITCH, min(FIND_MAX_PITCH, _number(pitch, "look.pitch_deg")))   # clamp, never exceed
        if height is not None and height not in HEIGHTS:
            raise PlanValidationError(f"look.height must be one of {list(HEIGHTS)} or null")
        return ToolCall("look", {"pitch_deg": pitch, "height": height})
    if set(args) != set(schema_props):
        raise PlanValidationError(f"{name}: expected arguments {sorted(schema_props)}, got {sorted(args)}")

    max_seconds, max_speed, max_degrees = (
        (FIND_MAX_SECONDS, FIND_MAX_SPEED, FIND_MAX_DEGREES) if find else (MAX_SECONDS, MAX_SPEED, MAX_DEGREES))
    if name == "found":
        label, where = args["label"], args["where"]
        if not isinstance(label, str) or not label.strip() or len(label) > FIND_LABEL_MAX:
            raise PlanValidationError(f"found.label must be a non-empty string of at most {FIND_LABEL_MAX} characters")
        if not isinstance(where, str) or len(where) > FIND_WHERE_MAX:
            raise PlanValidationError(f"found.where must be a string of at most {FIND_WHERE_MAX} characters")
        return ToolCall(name, {"label": label.strip(), "where": where.strip()})
    if name == "move":
        seconds = _number(args["seconds"], "move.seconds")
        speed = _number(args["speed"], "move.speed")
        if not MIN_SECONDS <= seconds <= max_seconds:
            raise PlanValidationError(f"move.seconds must be {MIN_SECONDS}-{max_seconds}")
        if not 0 <= speed <= max_speed:
            raise PlanValidationError(f"move.speed must be 0-{max_speed}")
        return ToolCall(name, {"direction": _enum(args["direction"], DIRECTIONS, "move.direction"),
                               "seconds": seconds, "speed": speed})
    if name == "turn":
        degrees = _number(args["degrees"], "turn.degrees")
        if not -max_degrees <= degrees <= max_degrees:
            raise PlanValidationError(f"turn.degrees must be -{max_degrees:g} to {max_degrees:g}")
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
