"""Robot Interface Specification for BARRY.

Defines the hardware-independent contract shared by every robot
implementation:

    ==============  ========================  ==============================
    RobotMode       Implementation            Runs on
    ==============  ========================  ==============================
    ``simulator``   ``RobotSimulator``        laptop / CI, no hardware
    ``remote``      ``RemoteRobotClient``     laptop, forwards to the Pi API
    ``dogzilla``    ``DogzillaController``    Raspberry Pi, drives the SDK
    ==============  ========================  ==============================

These are *deployment* configurations. Barry's three future user-facing
operating modes (hand, voice, LLM) sit on top of this interface and must not
need a different hardware layer.

Design rules (see ``backend/robot/robot_plan.md``):

* Value objects below validate their own ranges, so an invalid command can
  never reach an implementation. Implementations still clamp to the limits in
  ``backend.robot.config`` before touching hardware.
* A ``CommandResult`` with status ``accepted`` means "accepted and
  dispatched", never "physically executed". Nothing in this module claims
  verified physical motion.
* Telemetry separates *measured* hardware readings (may be ``None`` when a
  read fails) from *application-tracked* control state. Never fabricate a
  measurement.
* Stop always preserves the selected movement intensity and gait.

The legacy ``move/emergency_stop/reset_emergency_stop/get_status`` methods are
kept so ``backend/main.py`` keeps working until it is migrated to the v1 API.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ----------------------------------------------------------------------
# Enumerations
# ----------------------------------------------------------------------


class RobotMode(str, Enum):
    """Deployment configuration that selects the implementation."""

    SIMULATOR = "simulator"
    REMOTE = "remote"
    DOGZILLA = "dogzilla"


class Intensity(str, Enum):
    """Barry movement intensity. Maps to the Yahboom ``pace()`` mode.

    ``low -> "slow"``, ``medium -> "normal"``, ``high -> "high"``
    (see ``config.PACE_FOR_INTENSITY``). Pace is a stepping frequency, not a
    guaranteed linear speed.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Gait(str, Enum):
    """Gait type accepted by the Yahboom ``gait_type()`` SDK call."""

    TROT = "trot"
    WALK = "walk"
    HIGH_WALK = "high_walk"


class Direction(str, Enum):
    """Discrete movement directions.

    ``left``/``right`` are *sideways steps* (strafe, SDK ``left()/right()``).
    Turning in place uses ``turn_left``/``turn_right``
    (SDK ``turnleft()/turnright()``).
    """

    FORWARD = "forward"
    BACKWARD = "backward"
    LEFT = "left"
    RIGHT = "right"
    TURN_LEFT = "turn_left"
    TURN_RIGHT = "turn_right"


class HeightPreset(str, Enum):
    """Body height presets. Calibrated values live in ``config.HEIGHT_PRESETS_Z``."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CommandStatus(str, Enum):
    """Outcome of a command at the API level (never physical confirmation)."""

    ACCEPTED = "accepted"            # validated and dispatched to the robot
    STOP_DISPATCHED = "stop_dispatched"  # stop sent; physical stop not verified
    REJECTED = "rejected"            # refused by validation / safety policy
    FAILED = "failed"                # dispatch attempted but the backend errored


# ----------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------


def _require_finite(name: str, value: Any) -> float:
    """Returns ``value`` as float, raising ``ValueError`` if not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _require_unit_range(name: str, value: Any) -> float:
    """Validates a normalized joystick axis in ``[-1.0, 1.0]``."""
    number = _require_finite(name, value)
    if not -1.0 <= number <= 1.0:
        raise ValueError(f"{name} must be within [-1.0, 1.0], got {number}")
    return number


def apply_dead_zone(value: float, dead_zone: float) -> float:
    """Zeroes small joystick deflections and rescales the rest to keep full range.

    ``|value| <= dead_zone`` returns ``0.0``; otherwise the remaining travel is
    mapped linearly back onto ``(0, 1]`` so the output is continuous at the
    dead-zone edge. ``dead_zone`` must be in ``[0, 1)``.
    """
    if not 0.0 <= dead_zone < 1.0:
        raise ValueError(f"dead_zone must be within [0.0, 1.0), got {dead_zone}")
    magnitude = abs(value)
    if magnitude <= dead_zone:
        return 0.0
    scaled = (magnitude - dead_zone) / (1.0 - dead_zone)
    return math.copysign(min(scaled, 1.0), value)


# ----------------------------------------------------------------------
# Command value objects
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class MotionCommand:
    """Normalized analog joystick input (``POST /api/v1/motion``).

    :param forward: ``+1`` full forward, ``-1`` full backward.
    :param strafe:  ``+1`` full left, ``-1`` full right (SDK ``move_y`` sign;
                    verify against hardware and the Quest joystick).
    :param turn:    ``+1`` full turn left, ``-1`` full turn right.
    """

    forward: float = 0.0
    strafe: float = 0.0
    turn: float = 0.0

    def __post_init__(self) -> None:
        for name in ("forward", "strafe", "turn"):
            object.__setattr__(self, name, _require_unit_range(name, getattr(self, name)))

    @property
    def is_zero(self) -> bool:
        """True when every axis is exactly zero (equivalent to a stop)."""
        return self.forward == 0.0 and self.strafe == 0.0 and self.turn == 0.0


@dataclass(frozen=True)
class MovementStyle:
    """Movement intensity and gait (``POST /api/v1/movement-style``)."""

    intensity: Intensity = Intensity.MEDIUM
    gait: Gait = Gait.TROT

    def __post_init__(self) -> None:
        object.__setattr__(self, "intensity", Intensity(self.intensity))
        object.__setattr__(self, "gait", Gait(self.gait))


@dataclass(frozen=True)
class Posture:
    """Body height preset and orientation in degrees (``POST /api/v1/posture``).

    Angle *ranges* are checked against ``config.ATTITUDE_SAFE_LIMITS`` by the
    implementation, because limits are configurable; here we only check that
    the values are finite numbers.
    """

    height: HeightPreset = HeightPreset.MEDIUM
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "height", HeightPreset(self.height))
        for name in ("roll", "pitch", "yaw"):
            object.__setattr__(self, name, _require_finite(name, getattr(self, name)))


@dataclass(frozen=True)
class ActionInfo:
    """An approved preset action (``GET /api/v1/actions``)."""

    id: int
    name: str


# ----------------------------------------------------------------------
# Results and telemetry
# ----------------------------------------------------------------------


@dataclass
class CommandResult:
    """Uniform response for every command.

    ``status`` is about API acceptance only; see ``CommandStatus``.
    ``intensity`` is echoed by stop so callers can confirm it was preserved.
    """

    status: CommandStatus
    command: str
    message: str = ""
    intensity: Optional[Intensity] = None

    @property
    def ok(self) -> bool:
        """True for ``accepted`` and ``stop_dispatched``."""
        return self.status in (CommandStatus.ACCEPTED, CommandStatus.STOP_DISPATCHED)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready dict; omits ``intensity`` when not set."""
        data: Dict[str, Any] = {
            "status": self.status.value,
            "command": self.command,
            "message": self.message,
        }
        if self.intensity is not None:
            data["intensity"] = self.intensity.value
        return data


@dataclass
class Orientation:
    """Measured body orientation in degrees. ``None`` = reading unavailable."""

    roll: Optional[float] = None
    pitch: Optional[float] = None
    yaw: Optional[float] = None


@dataclass
class ControlState:
    """Application-tracked state: what Barry *commanded*, not what was measured."""

    intensity: Intensity = Intensity.MEDIUM
    gait: Gait = Gait.TROT
    posture: Posture = field(default_factory=Posture)
    moving: bool = False
    emergency_stopped: bool = False
    last_command: str = "stop"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intensity": self.intensity.value,
            "gait": self.gait.value,
            "posture": {
                "height": self.posture.height.value,
                "roll": self.posture.roll,
                "pitch": self.posture.pitch,
                "yaw": self.posture.yaw,
            },
            "moving": self.moving,
            "emergency_stopped": self.emergency_stopped,
            "last_command": self.last_command,
        }


@dataclass
class Telemetry:
    """``GET /api/v1/telemetry`` payload.

    ``battery``, ``orientation`` and ``firmware`` are *measured* and are
    ``None`` when the hardware read failed or is unavailable; ``read_errors``
    says why. ``control_state`` is *tracked* by the application.
    """

    battery: Optional[float] = None
    orientation: Orientation = field(default_factory=Orientation)
    firmware: Optional[str] = None
    read_errors: List[str] = field(default_factory=list)
    control_state: ControlState = field(default_factory=ControlState)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "battery": self.battery,
            "orientation": asdict(self.orientation),
            "firmware": self.firmware,
            "read_errors": list(self.read_errors),
            "control_state": self.control_state.to_dict(),
        }


# ----------------------------------------------------------------------
# The interface
# ----------------------------------------------------------------------


class RobotInterface(ABC):
    """Abstract base class that decouples the backend from physical hardware.

    Implementations must be thread-safe: the Pi server calls them from request
    handlers and from its watchdog at the same time. Every method returns a
    result instead of raising for expected failures (offline, rejected,
    e-stopped); only programming errors should raise.
    """

    # --- identity -----------------------------------------------------

    @property
    @abstractmethod
    def robot_mode(self) -> RobotMode:
        """Which deployment implementation this is."""

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Best-effort hardware/link health. ``False`` means commands will fail."""

    # --- movement -----------------------------------------------------

    @abstractmethod
    def move_analog(self, command: MotionCommand) -> CommandResult:
        """Continuous joystick movement.

        Applies ``config.dead_zone``, scales by the current intensity and
        ``config.max_motion_scale``, clamps to SDK limits and dispatches
        ``move_x / move_y / turn``. An all-zero command is treated as stop.
        Rejected while e-stopped or when the style/posture combo is unsafe.
        """

    @abstractmethod
    def move_discrete(self, direction: Direction, intensity: Optional[Intensity] = None) -> CommandResult:
        """One directional step command (``forward/back/left/right/turnleft/turnright``).

        ``intensity`` overrides the step size for this command only; it does
        **not** change the stored movement style. Subject to the same safety
        rules (watchdog, e-stop) as analog motion.
        """

    @abstractmethod
    def stop(self) -> CommandResult:
        """Stops locomotion immediately (SDK ``stop()``).

        Always allowed, including while e-stopped. Must preserve the stored
        intensity and gait and return them via ``CommandResult.intensity``.
        Returns ``stop_dispatched``: a physical stop is not verified.
        """

    # --- style & posture ----------------------------------------------

    @abstractmethod
    def set_movement_style(self, style: MovementStyle) -> CommandResult:
        """Sets pace (from intensity) and gait. Must not change body height."""

    @abstractmethod
    def set_posture(self, posture: Optional[Posture] = None, **legacy: Any) -> Any:
        """Sets body height preset and roll/pitch/yaw; returns a ``CommandResult``.

        Rejects angles outside ``config.ATTITUDE_SAFE_LIMITS`` and unsafe
        style/posture combinations. Not the same as ``reset()``.

        Legacy form: called with keyword arguments only (``height_z``,
        ``gait_type``, ``pace``, ``preset``, ...), as ``backend/main.py`` does,
        implementations delegate to ``backend.robot.legacy.apply_legacy_posture``
        and return the legacy ``{"success", "message", "telemetry"}`` dict.
        """

    # --- IMU self-stabilization (optional capability) ------------------

    @property
    def imu_enabled(self) -> bool:
        """Tracked IMU self-stabilization flag. ``False`` when unsupported."""
        return False

    def set_imu(self, enabled: bool) -> CommandResult:
        """Toggles IMU self-stabilization (SDK ``imu(0|1)``).

        Not part of the v1 Quest controls; only the legacy posture API uses it.
        Default: rejected, for implementations without the capability.
        """
        return CommandResult(CommandStatus.REJECTED, "imu", "IMU control is not supported by this robot.")

    # --- actions ------------------------------------------------------

    @abstractmethod
    def list_actions(self) -> List[ActionInfo]:
        """Approved preset actions only (``config.APPROVED_ACTIONS``)."""

    @abstractmethod
    def perform_action(self, action_id: int) -> CommandResult:
        """Runs an approved preset action (SDK ``action(id)``).

        Rejects unknown IDs, and rejects while moving or e-stopped.
        """

    @abstractmethod
    def reset(self) -> CommandResult:
        """Full robot reset (SDK ``reset()`` == ``action(255)``).

        Broader than a posture reset: stops motion and restores the robot's
        initial parameters. Tracked posture returns to the neutral preset;
        intensity and gait are re-applied afterwards so Barry state and robot
        state stay in sync. The UI must confirm before calling this.
        """

    # --- state --------------------------------------------------------

    @abstractmethod
    def read_telemetry(self) -> Telemetry:
        """Measured readings (battery, orientation, firmware) plus tracked state."""

    @abstractmethod
    def get_control_state(self) -> ControlState:
        """Tracked control state only; never touches hardware."""

    # --- emergency stop latch ----------------------------------------

    @abstractmethod
    def emergency_stop(self) -> Dict[str, Any]:
        """Stops and latches: all motion, posture and action commands are
        rejected until ``reset_emergency_stop()``. Software latch only; a
        physical emergency-stop procedure is still required.
        """

    @abstractmethod
    def reset_emergency_stop(self) -> Dict[str, Any]:
        """Clears the emergency stop latch. Does not move the robot."""

    # --- legacy API (backend/main.py, /api/robot/*) --------------------

    @abstractmethod
    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        """Legacy one-shot movement used by ``backend/main.py``.

        ``action`` is ``forward/backward/left/right/stop/emergency_stop``.
        Kept until the laptop backend migrates to the v1 methods above.
        """

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Legacy status dict (``RobotStatusResponse`` shape in ``backend/models.py``)."""
