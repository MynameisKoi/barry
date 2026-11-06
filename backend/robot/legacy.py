"""Compatibility layer for the laptop's legacy posture API (``backend/main.py``).

``backend/main.py`` calls ``robot.set_posture(height_z=..., roll=..., pitch=...,
yaw=..., gait_type=..., pace=..., imu_enabled=..., preset=...)`` and expects a
``{"success", "message", "telemetry"}`` dict whose telemetry carries
``posture`` and ``imu`` blocks. The v1 contract instead splits this into
``set_movement_style(MovementStyle)`` and ``set_posture(Posture)`` with
calibrated height *presets*. This module translates one into the other so
``backend/main.py`` keeps working unchanged until it migrates to v1.

Translation rules
-----------------
* ``pace`` -> ``Intensity`` (inverse of ``config.PACE_FOR_INTENSITY``),
  ``gait_type`` -> ``Gait``.
* ``height_z`` (mm) -> the nearest ``HeightPreset`` in
  ``config.HEIGHT_PRESETS_Z``. This is lossy on purpose: v1 only commands
  calibrated heights. The reported ``height_z`` is the preset's value.
* ``preset``: ``stand`` / ``crouch`` / ``obstacle_crossing`` expand to
  gait/pace/height exactly as the legacy implementation defined them.
* ``roll/pitch/yaw`` not given keep their current values.
* ``imu_enabled`` -> ``robot.set_imu()`` (not every implementation supports it).
* Every v1 safety rule still applies (attitude limits, unsafe combinations,
  e-stop latch): a legacy call can never do what a v1 call could not.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from backend.robot.config import HEIGHT_PRESETS_Z, PACE_FOR_INTENSITY, check_combination
from backend.robot.interface import ControlState, Gait, HeightPreset, Intensity, MovementStyle, Posture, RobotInterface

#: Legacy presets: (gait or None = keep, pace or None = keep, height_z mm).
LEGACY_PRESETS: Dict[str, Tuple[Optional[str], Optional[str], float]] = {
    "stand": ("trot", "normal", 105.0),
    "crouch": (None, "slow", 75.0),
    "obstacle_crossing": ("high_walk", "slow", 95.0),
}

_INTENSITY_FOR_PACE = {pace: intensity for intensity, pace in PACE_FOR_INTENSITY.items()}


def nearest_height_preset(height_z: float) -> HeightPreset:
    """Maps a raw body height in mm onto the closest calibrated preset."""
    return min(HEIGHT_PRESETS_Z, key=lambda preset: abs(HEIGHT_PRESETS_Z[preset] - height_z))


def _fail(robot: RobotInterface, message: str) -> Dict[str, Any]:
    return {"success": False, "message": message, "telemetry": robot.get_status()}


def apply_legacy_posture(
    robot: RobotInterface,
    *,
    height_z: Optional[float] = None,
    roll: Optional[float] = None,
    pitch: Optional[float] = None,
    yaw: Optional[float] = None,
    gait_type: Optional[str] = None,
    pace: Optional[str] = None,
    imu_enabled: Optional[bool] = None,
    preset: Optional[str] = None,
) -> Dict[str, Any]:
    """Applies a legacy posture request through the v1 methods of ``robot``.

    Validates the *combined* target first, then applies style and posture in
    whichever order keeps every intermediate state valid. Returns the legacy
    ``{"success", "message", "telemetry"}`` dict.
    """
    state = robot.get_control_state()

    # 1. Expand the preset, then let explicit fields override it.
    if preset is not None:
        key = preset.strip().lower()
        if key not in LEGACY_PRESETS:
            return _fail(robot, f"Unknown posture preset '{preset}'. Use one of: {', '.join(LEGACY_PRESETS)}.")
        preset_gait, preset_pace, preset_z = LEGACY_PRESETS[key]
        gait_type = gait_type if gait_type is not None else preset_gait
        pace = pace if pace is not None else preset_pace
        height_z = height_z if height_z is not None else preset_z

    # 2. Translate to v1 values.
    try:
        gait = Gait(gait_type.strip().lower()) if gait_type is not None else state.gait
    except ValueError:
        return _fail(robot, f"Invalid gait_type '{gait_type}'. Must be trot, walk, or high_walk.")
    if pace is not None and pace.strip().lower() not in _INTENSITY_FOR_PACE:
        return _fail(robot, f"Invalid pace '{pace}'. Must be slow, normal, or high.")
    intensity = _INTENSITY_FOR_PACE[pace.strip().lower()] if pace is not None else state.intensity
    height = nearest_height_preset(float(height_z)) if height_z is not None else state.posture.height
    try:
        posture = Posture(
            height=height,
            roll=state.posture.roll if roll is None else roll,
            pitch=state.posture.pitch if pitch is None else pitch,
            yaw=state.posture.yaw if yaw is None else yaw,
        )
    except ValueError as exc:
        return _fail(robot, str(exc))

    reason = check_combination(gait, intensity, height)
    if reason:
        return _fail(robot, reason)

    # 3. Apply. Style is checked against the current height and posture
    #    against the current style, so try the order that is valid now.
    style_changed = (gait, intensity) != (state.gait, state.intensity)
    posture_changed = posture != state.posture
    steps = []
    if style_changed and check_combination(gait, intensity, state.posture.height) is None:
        steps = ["style", "posture"]
    else:
        steps = ["posture", "style"]
    for step in steps:
        if step == "style" and style_changed:
            result = robot.set_movement_style(MovementStyle(intensity, gait))
        elif step == "posture" and posture_changed:
            result = robot.set_posture(posture)
        else:
            continue
        if not result.ok:
            return _fail(robot, result.message or f"{step} update rejected")

    if imu_enabled is not None:
        result = robot.set_imu(bool(imu_enabled))
        if not result.ok:
            return _fail(robot, result.message)

    return {"success": True, "message": "Posture updated.", "telemetry": robot.get_status()}


def legacy_status_blocks(robot: RobotInterface, state: Optional[ControlState] = None) -> Dict[str, Any]:
    """``posture`` and ``imu`` blocks for the legacy ``get_status()`` dict.

    Values are the *commanded* (tracked) state, not IMU measurements.
    """
    state = state if state is not None else robot.get_control_state()
    return {
        "posture": {
            "height_z": float(HEIGHT_PRESETS_Z[state.posture.height]),
            "gait_type": state.gait.value,
            "pace": PACE_FOR_INTENSITY[state.intensity],
        },
        "imu": {
            "imu_stabilization": robot.imu_enabled,
            "roll": state.posture.roll,
            "pitch": state.posture.pitch,
            "yaw": state.posture.yaw,
        },
    }
