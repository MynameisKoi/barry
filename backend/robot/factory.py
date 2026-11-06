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

from typing import Optional

from backend.robot.config import RobotConfig
from backend.robot.interface import RobotInterface, RobotMode


def create_robot(config: Optional[RobotConfig] = None) -> RobotInterface:
    """Builds the robot implementation for ``config.mode``.

    :param config: Deployment settings; ``RobotConfig.from_env()`` when omitted.
    :raises ValueError: for an unknown mode (cannot happen for a ``RobotMode``
        value, kept as a guard for future enum additions).
    """
    config = config if config is not None else RobotConfig.from_env()

    if config.mode == RobotMode.SIMULATOR:
        from backend.robot.simulator import RobotSimulator

        return RobotSimulator(config)

    if config.mode == RobotMode.REMOTE:
        from backend.robot.remote import RemoteRobotClient

        return RemoteRobotClient(config)

    if config.mode == RobotMode.DOGZILLA:
        from backend.robot.dogzilla import DogzillaController

        # The current controller takes the serial device as ``device_endpoint``.
        # Hardware-controller task may change this signature; keep in sync.
        return DogzillaController(device_endpoint=config.dogzilla_port)

    raise ValueError(f"Unsupported robot mode: {config.mode!r}")
