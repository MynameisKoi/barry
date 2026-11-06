"""Factory that selects the ``RobotInterface`` implementation.

``create_robot()`` reads ``RobotConfig.mode`` (``ROBOT_MODE``) and returns:

==============  ===============================  =============================
``mode``        Class                            Where it runs
==============  ===============================  =============================
``simulator``   ``simulator.RobotSimulator``     laptop / CI, no hardware
``remote``      ``remote.RemoteRobotClient``     laptop, talks to the Pi API
``dogzilla``    ``dogzilla.DogzillaController``  Raspberry Pi, drives the SDK
==============  ===============================  =============================

Imports are done lazily inside each branch so a laptop running ``simulator`` or
``remote`` never imports the hardware/SDK module, and the Pi never imports
``httpx`` client code it does not need.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional, Union

from backend.robot.config import RobotConfig
from backend.robot.interface import RobotInterface, RobotMode


def create_robot(
    mode: Union[RobotConfig, RobotMode, str, None] = None,
    config: Optional[RobotConfig] = None,
) -> RobotInterface:
    """Builds the robot implementation for the selected mode.

    Accepted call forms:

    * ``create_robot(config)`` / ``create_robot(config=config)`` -- v1 form.
    * ``create_robot("simulation")`` / ``create_robot(mode="remote")`` -- the
      form ``backend/main.py`` uses. ``mode`` overrides ``config.mode`` (or the
      ``ROBOT_MODE`` env var); legacy ``"simulation"`` means ``simulator``.

    :raises ValueError: for an unknown mode string.
    """
    if isinstance(mode, RobotConfig):
        config, mode = mode, None
    config = config if config is not None else RobotConfig.from_env()
    if mode is not None:
        raw = mode.value if isinstance(mode, RobotMode) else str(mode).strip().lower()
        raw = "simulator" if raw == "simulation" else raw
        try:
            config = replace(config, mode=RobotMode(raw))
        except ValueError as exc:
            valid = ", ".join(m.value for m in RobotMode)
            raise ValueError(f"Unsupported robot mode {mode!r}; use one of {valid}") from exc

    if config.mode == RobotMode.SIMULATOR:
        from backend.robot.simulator import RobotSimulator

        return RobotSimulator(config)

    if config.mode == RobotMode.REMOTE:
        from backend.robot.remote import RemoteRobotClient

        return RemoteRobotClient(config)

    if config.mode == RobotMode.DOGZILLA:
        from backend.robot.dogzilla import DogzillaController

        return DogzillaController(config)

    raise ValueError(f"Unsupported robot mode: {config.mode!r}")
