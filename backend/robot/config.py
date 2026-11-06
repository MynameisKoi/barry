"""Robot configuration for BARRY.

Two kinds of settings live here:

1. **Constants** taken from Yahboom's ``DOGZILLALib.py`` (V2.0.7) and Barry's
   conservative defaults: SDK ranges, intensity/pace mapping, height presets,
   attitude limits, the action allowlist and unsafe combinations.
   Values marked ``UNCALIBRATED`` must be verified on the real DOGZILLA S2.
2. **Deployment settings** read from environment variables by
   ``RobotConfig.from_env()``.

This module must stay free of hardware and laptop-only imports so it can run
on the Raspberry Pi with minimal dependencies.

Environment variables
---------------------
=========================  ===========================  ==================================
Variable                   Default                      Purpose
=========================  ===========================  ==================================
``ROBOT_MODE``             ``simulator``                ``simulator`` / ``remote`` / ``dogzilla``
``ROBOT_API_KEY``          (none)                       Shared secret, ``X-Robot-Key`` header
``ROBOT_REMOTE_URL``       ``http://barry-pi.local:8100`` Pi Robot API base URL (laptop)
``ROBOT_REMOTE_TIMEOUT``   ``0.5``                      Per-request timeout, seconds (laptop)
``DOGZILLA_PORT``          ``/dev/ttyAMA0``             Serial device (Pi)
``ROBOT_WATCHDOG_MS``      ``500``                      Stop if no motion command for this long
``ROBOT_SESSION_TTL_S``    ``10``                       Idle control session expiry
``ROBOT_MAX_COMMAND_AGE_MS`` ``0``                      Reject commands older than this by
                                                        sender clock; ``0`` disables (needs NTP)
``ROBOT_DEAD_ZONE``        ``0.1``                      Joystick dead zone, ``[0, 1)``
``ROBOT_MAX_MOTION_SCALE`` ``0.6``                      Fraction of SDK range ever commanded
=========================  ===========================  ==================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, FrozenSet, Mapping, Optional, Tuple

from backend.robot.interface import Gait, HeightPreset, Intensity, RobotMode

# ----------------------------------------------------------------------
# SDK limits (DOGZILLALib.py PARAM table and method-level clamps)
# ----------------------------------------------------------------------

#: ``move_x(step)`` clamps to +/-20 (PARAM VX_LIMIT is 25, the method clamps tighter).
SDK_VX_MAX = 20
#: ``move_y(step)`` clamps to +/-18.
SDK_VY_MAX = 18
#: ``turn(step)`` clamps to +/-70 and raises any non-zero |step| < 30 to 30.
SDK_VYAW_MAX = 70
SDK_VYAW_MIN_NONZERO = 30
#: ``translation('z', v)`` accepts 75..115 (body height).
SDK_Z_RANGE: Tuple[int, int] = (75, 115)
#: ``attitude()`` PARAM limits in degrees.
SDK_ATTITUDE_MAX: Mapping[str, float] = {"roll": 20.0, "pitch": 15.0, "yaw": 11.0}
#: ``action(255)`` is what ``reset()`` sends; never allow it as a preset action.
SDK_RESET_ACTION_ID = 255

# ----------------------------------------------------------------------
# Barry mappings and conservative defaults
# ----------------------------------------------------------------------

#: Barry intensity -> SDK ``pace()`` mode.
PACE_FOR_INTENSITY: Mapping[Intensity, str] = {
    Intensity.LOW: "slow",
    Intensity.MEDIUM: "normal",
    Intensity.HIGH: "high",
}

#: Scale applied to analog motion per intensity, on top of ``max_motion_scale``.
MOTION_SCALE_FOR_INTENSITY: Mapping[Intensity, float] = {
    Intensity.LOW: 0.4,
    Intensity.MEDIUM: 0.7,
    Intensity.HIGH: 1.0,
}

#: Step argument for discrete ``forward/back/left/right/turnleft/turnright``.
#: UNCALIBRATED. Turn steps respect the SDK minimum of 30.
DISCRETE_STEP_FOR_INTENSITY: Mapping[Intensity, Dict[str, int]] = {
    Intensity.LOW: {"linear": 6, "turn": 30},
    Intensity.MEDIUM: {"linear": 10, "turn": 40},
    Intensity.HIGH: {"linear": 14, "turn": 50},
}

#: Height preset -> ``translation('z', value)``. UNCALIBRATED; within SDK_Z_RANGE.
HEIGHT_PRESETS_Z: Mapping[HeightPreset, int] = {
    HeightPreset.LOW: 85,
    HeightPreset.MEDIUM: 95,
    HeightPreset.HIGH: 105,
}

#: Barry's attitude limits in degrees (tighter than SDK_ATTITUDE_MAX). UNCALIBRATED.
ATTITUDE_SAFE_LIMITS: Mapping[str, float] = {"roll": 15.0, "pitch": 10.0, "yaw": 8.0}

#: Approved preset actions. Names come from Yahboom joystick source comments
#: and must be verified on the DOGZILLA S2 before relying on them.
APPROVED_ACTIONS: Mapping[int, str] = {
    10: "three_axis",
    11: "pee",
    16: "swing",
}

#: (gait, intensity, height) combinations that are rejected. ``None`` = any.
#: Conservative placeholders until hardware testing says otherwise.
UNSAFE_COMBINATIONS: FrozenSet[Tuple[Optional[Gait], Optional[Intensity], Optional[HeightPreset]]] = frozenset(
    {
        # High-stepping gait at the fastest pace.
        (Gait.HIGH_WALK, Intensity.HIGH, None),
        # High-stepping gait with the body lowered: feet may hit the body.
        (Gait.HIGH_WALK, None, HeightPreset.LOW),
    }
)


def check_combination(gait: Gait, intensity: Intensity, height: HeightPreset) -> Optional[str]:
    """Returns a human-readable reason if the combination is unsafe, else ``None``.

    Every implementation calls this from ``set_movement_style`` and
    ``set_posture`` so the rule is enforced identically everywhere.
    """
    for bad_gait, bad_intensity, bad_height in UNSAFE_COMBINATIONS:
        if (
            (bad_gait is None or bad_gait == gait)
            and (bad_intensity is None or bad_intensity == intensity)
            and (bad_height is None or bad_height == height)
        ):
            return (
                f"Unsafe combination: gait={gait.value}, intensity={intensity.value}, "
                f"height={height.value}"
            )
    return None


def check_attitude(roll: float, pitch: float, yaw: float) -> Optional[str]:
    """Returns a reason if any angle exceeds ``ATTITUDE_SAFE_LIMITS``, else ``None``."""
    for name, value in (("roll", roll), ("pitch", pitch), ("yaw", yaw)):
        limit = ATTITUDE_SAFE_LIMITS[name]
        if abs(value) > limit:
            return f"{name}={value} exceeds safe limit of +/-{limit} degrees"
    return None


# ----------------------------------------------------------------------
# Deployment settings
# ----------------------------------------------------------------------


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class RobotConfig:
    """Deployment settings. Build with ``RobotConfig.from_env()`` or directly in tests."""

    mode: RobotMode = RobotMode.SIMULATOR
    api_key: Optional[str] = None
    remote_url: str = "http://barry-pi.local:8100"
    remote_timeout_s: float = 0.5
    dogzilla_port: str = "/dev/ttyAMA0"
    watchdog_ms: int = 500
    session_ttl_s: float = 10.0
    max_command_age_ms: int = 0
    dead_zone: float = 0.1
    max_motion_scale: float = 0.6

    def __post_init__(self) -> None:
        if not 0.0 <= self.dead_zone < 1.0:
            raise ValueError("ROBOT_DEAD_ZONE must be within [0.0, 1.0)")
        if not 0.0 < self.max_motion_scale <= 1.0:
            raise ValueError("ROBOT_MAX_MOTION_SCALE must be within (0.0, 1.0]")
        if self.watchdog_ms <= 0:
            raise ValueError("ROBOT_WATCHDOG_MS must be positive")
        if self.session_ttl_s <= 0:
            raise ValueError("ROBOT_SESSION_TTL_S must be positive")
        if self.max_command_age_ms < 0:
            raise ValueError("ROBOT_MAX_COMMAND_AGE_MS must be >= 0")
        if self.remote_timeout_s <= 0:
            raise ValueError("ROBOT_REMOTE_TIMEOUT must be positive")

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "RobotConfig":
        """Reads settings from ``env`` (defaults to ``os.environ``).

        Accepts the legacy ``ROBOT_MODE=simulation`` as ``simulator``.
        """
        env = os.environ if env is None else env
        raw_mode = env.get("ROBOT_MODE", "simulator").strip().lower()
        if raw_mode == "simulation":
            raw_mode = "simulator"
        try:
            mode = RobotMode(raw_mode)
        except ValueError as exc:
            valid = ", ".join(m.value for m in RobotMode)
            raise ValueError(f"ROBOT_MODE must be one of {valid}, got {raw_mode!r}") from exc

        return cls(
            mode=mode,
            api_key=env.get("ROBOT_API_KEY") or None,
            remote_url=env.get("ROBOT_REMOTE_URL", cls.remote_url).rstrip("/"),
            remote_timeout_s=_env_float(env, "ROBOT_REMOTE_TIMEOUT", cls.remote_timeout_s),
            dogzilla_port=env.get("DOGZILLA_PORT", cls.dogzilla_port),
            watchdog_ms=int(_env_float(env, "ROBOT_WATCHDOG_MS", cls.watchdog_ms)),
            session_ttl_s=_env_float(env, "ROBOT_SESSION_TTL_S", cls.session_ttl_s),
            max_command_age_ms=int(_env_float(env, "ROBOT_MAX_COMMAND_AGE_MS", cls.max_command_age_ms)),
            dead_zone=_env_float(env, "ROBOT_DEAD_ZONE", cls.dead_zone),
            max_motion_scale=_env_float(env, "ROBOT_MAX_MOTION_SCALE", cls.max_motion_scale),
        )
