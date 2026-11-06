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

import os
from typing import Optional, Union

from backend.robot.config import RobotConfig
from backend.robot.interface import RobotInterface, RobotMode


def create_robot(
    config: Optional[Union[RobotConfig, str]] = None,
    mode: Optional[str] = None,
) -> RobotInterface:
    """Builds the robot implementation for ``config.mode``.

    :param config: Deployment settings; ``RobotConfig.from_env()`` when omitted.
    :param mode: Optional legacy mode string (e.g. 'simulator', 'remote', 'dogzilla').
    :raises ValueError: for an unknown mode.
    """
    if isinstance(config, str):
        if mode is not None:
            raise ValueError("Pass either config as a mode string or mode, not both")
        mode = config
        config = None
    elif config is not None and mode is not None:
        raise ValueError("mode cannot be combined with a RobotConfig")

    if config is None:
        env = dict(os.environ)
        if mode:
            env["ROBOT_MODE"] = mode
        config = RobotConfig.from_env(env=env)

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
