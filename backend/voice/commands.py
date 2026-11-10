"""Deterministic fixed-command matcher for voice mode. No LLM.

"stop" and "emergency stop" are matched first, anywhere in the utterance, so they win over
any motion words around them ("forward ten seconds ... stop" is a stop).  Motion commands are
bounded: durations and angles are clamped, and missing values get a short default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_SECONDS = 1.0
MAX_SECONDS = 10.0
DEFAULT_DEGREES = 90.0
MAX_DEGREES = 180.0

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20,
    "thirty": 30, "forty five": 45, "forty-five": 45, "forty": 40, "sixty": 60,
    "ninety": 90, "one eighty": 180, "hundred eighty": 180,
}
_NUMBER = (
    r"(\d+(?:\.\d+)?|"
    + "|".join(sorted((re.escape(w) for w in _WORD_NUMBERS), key=len, reverse=True))
    + ")"
)

_EMERGENCY = re.compile(r"\be[\s-]*stop\b|\bemergency[\s,.-]*stop\b|\bstop[\s,.-]*emergency\b")
_STOP = re.compile(r"\bstop\b")
_MOVE = re.compile(
    r"\b(?P<dir>forwards?|back(?:wards?)?)\b"
    rf"(?:\s+(?:for\s+)?{_NUMBER}\s*(?:seconds?|secs?|s)\b)?"
)
_TURN = re.compile(
    r"\bturn\s+(?P<side>left|right)\b"
    rf"(?:\s+(?:by\s+)?{_NUMBER}\s*(?:degrees?|deg)\b)?"
)


@dataclass(frozen=True)
class CommandMatch:
    command: str | None = None
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return self.command is not None


def _number(token: str | None) -> float | None:
    if token is None:
        return None
    if token in _WORD_NUMBERS:
        return float(_WORD_NUMBERS[token])
    return float(token)


def _clamp(value: float | None, default: float, maximum: float) -> float:
    if value is None:
        return default
    return min(max(value, 0.0), maximum)


def match_command(transcript: str) -> CommandMatch:
    """Map a transcript to one fixed command; unrecognised text matches nothing."""
    text = " ".join((transcript or "").lower().split())
    if not text:
        return CommandMatch()

    # Safety first: stop / emergency stop win over everything else in the utterance.
    if _EMERGENCY.search(text):
        return CommandMatch("emergency_stop")
    if _STOP.search(text):
        return CommandMatch("stop")

    candidates: list[tuple[int, CommandMatch]] = []
    move = _MOVE.search(text)
    if move:
        command = "forward" if move.group("dir").startswith("forward") else "back"
        seconds = _clamp(_number(move.group(2)), DEFAULT_SECONDS, MAX_SECONDS)
        candidates.append((move.start(), CommandMatch(command, {"seconds": seconds})))
    turn = _TURN.search(text)
    if turn:
        degrees = _clamp(_number(turn.group(2)), DEFAULT_DEGREES, MAX_DEGREES)
        candidates.append((turn.start(), CommandMatch(f"turn_{turn.group('side')}", {"degrees": degrees})))
    if not candidates:
        return CommandMatch()
    return min(candidates, key=lambda c: c[0])[1]
