"""Factory for instantiating the configured `RobotInterface` (`backend/robot/factory.py`)."""

from typing import Optional

from backend.robot.config import RobotConfig, get_robot_config
from backend.robot.dogzilla import DogzillaController
from backend.robot.interface import RobotInterface
from backend.robot.remote import RemoteRobotClient
from backend.robot.simulator import RobotSimulator


def create_robot(
    mode: Optional[str] = None,
    config: Optional[RobotConfig] = None,
) -> RobotInterface:
    """Instantiates `RobotSimulator`, `RemoteRobotClient`, or `DogzillaController` based on `ROBOT_MODE`."""
    cfg = config or get_robot_config()
    resolved_mode = (mode or cfg.ROBOT_MODE or "simulation").strip().lower()

    if resolved_mode == "simulation":
        return RobotSimulator()
    if resolved_mode == "remote":
        return RemoteRobotClient(
            base_url=cfg.ROBOT_REMOTE_URL,
            api_key=cfg.ROBOT_API_KEY,
            timeout=cfg.ROBOT_REMOTE_TIMEOUT,
        )
    if resolved_mode in ("dogzilla", "physical"):
        return DogzillaController(
            device_endpoint=cfg.DOGZILLA_PORT,
            max_speed=cfg.ROBOT_MAX_SPEED,
        )

    raise ValueError(
        f"Unsupported ROBOT_MODE '{resolved_mode}'. "
        "Supported values: simulation, remote, dogzilla"
    )
