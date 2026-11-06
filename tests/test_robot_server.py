"""Contract-level tests for the Pi-side Robot API."""

import threading
import time
from typing import Any, Dict, Optional

from fastapi.testclient import TestClient

from backend.robot.config import APPROVED_ACTIONS, RobotConfig
from backend.robot.interface import (
    ActionInfo, CommandResult, CommandStatus, ControlState, Direction, Intensity,
    MotionCommand, MovementStyle, Posture, RobotInterface, RobotMode, Telemetry,
)
from backend.robot.server import create_app


class RecordingRobot(RobotInterface):
    """Small synchronous fake; intentionally independent of simulator work."""

    def __init__(self) -> None:
        self.state = ControlState()
        self.is_connected = True
        self.calls: list[str] = []
        self.motion_started = threading.Event()
        self.release_motion = threading.Event()
        self.block_motion = False

    @property
    def robot_mode(self) -> RobotMode:
        return RobotMode.SIMULATOR

    @property
    def connected(self) -> bool:
        return self.is_connected

    def accepted(self, command: str) -> CommandResult:
        self.calls.append(command)
        return CommandResult(CommandStatus.ACCEPTED, command)

    def move_analog(self, command: MotionCommand) -> CommandResult:
        self.motion_started.set()
        if self.block_motion:
            self.release_motion.wait(2)
        self.state.moving = not command.is_zero
        return self.accepted("motion")

    def move_discrete(self, direction: Direction, intensity: Optional[Intensity] = None) -> CommandResult:
        self.state.moving = True
        return self.accepted("motion_discrete")

    def stop(self) -> CommandResult:
        self.calls.append("stop")
        self.state.moving = False
        return CommandResult(CommandStatus.STOP_DISPATCHED, "stop", intensity=self.state.intensity)

    def set_movement_style(self, style: MovementStyle) -> CommandResult:
        self.state.intensity, self.state.gait = style.intensity, style.gait
        return self.accepted("movement_style")

    def set_posture(self, posture: Posture) -> CommandResult:
        self.state.posture = posture
        return self.accepted("posture")

    def list_actions(self) -> list[ActionInfo]:
        return [ActionInfo(key, value) for key, value in APPROVED_ACTIONS.items()]

    def perform_action(self, action_id: int) -> CommandResult:
        return self.accepted("action")

    def reset(self) -> CommandResult:
        return self.accepted("reset")

    def read_telemetry(self) -> Telemetry:
        return Telemetry(battery=88.0, firmware="test", control_state=self.state)

    def get_control_state(self) -> ControlState:
        return self.state

    def emergency_stop(self) -> Dict[str, Any]:
        self.calls.append("emergency_stop")
        self.state.emergency_stopped = True
        return {"success": True}

    def reset_emergency_stop(self) -> Dict[str, Any]:
        self.state.emergency_stopped = False
        return {"success": True}

    def move(self, action: str, speed: float = 0.2) -> Dict[str, Any]:
        return {"success": True}

    def get_status(self) -> Dict[str, Any]:
        return {"connected": True}


def setup(watchdog_ms: int = 500):
    robot = RecordingRobot()
    config = RobotConfig(api_key="secret", watchdog_ms=watchdog_ms)
    return robot, create_app(robot, config)


def auth() -> dict[str, str]:
    return {"X-Robot-Key": "secret"}


def session(client: TestClient) -> tuple[str, dict[str, str]]:
    sid = client.post("/api/v1/session", headers=auth()).json()["session_id"]
    headers = {**auth(), "X-Control-Session": sid, "X-Command-Seq": "1"}
    return sid, headers


def test_auth_session_conflict_renewal_and_stale_sequence():
    _, app = setup()
    with TestClient(app) as client:
        assert client.get("/api/v1/status").status_code == 401
        sid, headers = session(client)
        assert client.post("/api/v1/session", headers=auth()).status_code == 409
        renewed = client.post("/api/v1/session", headers={**auth(), "X-Control-Session": sid})
        assert renewed.status_code == 200
        assert renewed.json()["session_id"] == sid
        body = {"forward": 0.5, "strafe": 0, "turn": 0}
        assert client.post("/api/v1/motion", headers=headers, json=body).status_code == 200
        assert client.post("/api/v1/motion", headers=headers, json=body).status_code == 409


def test_stop_and_emergency_stop_need_no_session():
    robot, app = setup()
    with TestClient(app) as client:
        assert client.post("/api/v1/stop", headers=auth()).status_code == 200
        emergency = client.post("/api/v1/emergency-stop", headers=auth())
        assert emergency.status_code == 200
        assert emergency.json() == {
            "status": "stop_dispatched", "command": "emergency_stop",
            "message": "", "intensity": "medium",
        }
        assert robot.calls[-2:] == ["stop", "emergency_stop"]


def test_stop_invalidates_motion_waiting_for_dispatch_lock():
    robot, app = setup()
    with TestClient(app) as client:
        _, first = session(client)
        second = {**first, "X-Command-Seq": "2"}
        robot.block_motion = True
        results: list[int] = []
        one = threading.Thread(target=lambda: results.append(client.post(
            "/api/v1/motion", headers=first, json={"forward": 1, "strafe": 0, "turn": 0}).status_code))
        two = threading.Thread(target=lambda: results.append(client.post(
            "/api/v1/motion", headers=second, json={"forward": 1, "strafe": 0, "turn": 0}).status_code))
        one.start()
        assert robot.motion_started.wait(1)
        two.start()
        time.sleep(0.03)
        assert client.post("/api/v1/stop", headers=auth()).status_code == 200
        robot.release_motion.set()
        one.join(2)
        two.join(2)
        assert results == [409, 409]


def test_watchdog_unknown_action_telemetry_status_and_shutdown_stop():
    robot, app = setup(watchdog_ms=30)
    with TestClient(app) as client:
        startup_stops = robot.calls.count("stop")
        _, headers = session(client)
        response = client.post("/api/v1/motion", headers=headers,
                               json={"forward": 1, "strafe": 0, "turn": 0})
        assert response.status_code == 200
        deadline = time.time() + 1
        while robot.calls.count("stop") == startup_stops and time.time() < deadline:
            time.sleep(0.01)
        assert robot.calls.count("stop") > startup_stops
        bad = client.post("/api/v1/actions/999", headers={**headers, "X-Command-Seq": "2"})
        assert bad.status_code == 422
        telemetry = client.get("/api/v1/telemetry", headers=auth()).json()
        assert telemetry["battery"] == 88.0
        assert "control_state" in telemetry
        status = client.get("/api/v1/status", headers=auth()).json()
        assert set(("robot_mode", "connected", "control_session_active",
                    "last_command_age_ms", "watchdog", "intensity", "gait")) <= status.keys()
    assert robot.calls[-1] == "stop"


def test_emergency_reset_requires_session_and_sequence():
    _, app = setup()
    with TestClient(app) as client:
        assert client.post("/api/v1/emergency-stop/reset", headers=auth()).status_code == 409
        _, headers = session(client)
        response = client.post("/api/v1/emergency-stop/reset", headers=headers)
        assert response.status_code == 200
        assert response.json() == {
            "status": "accepted", "command": "emergency_reset", "message": "",
        }


def test_disconnected_telemetry_returns_readings_and_errors():
    robot, app = setup()
    robot.is_connected = False
    with TestClient(app) as client:
        response = client.get("/api/v1/telemetry", headers=auth())
        assert response.status_code == 200
        assert response.json()["battery"] == 88.0
