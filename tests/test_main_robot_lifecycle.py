"""Robot lifecycle in backend.main: lazy creation, 503 before startup, stop on shutdown."""

from fastapi.testclient import TestClient

import backend.main
from backend.main import app
from backend.robot.simulator import RobotSimulator


class SpyRobot(RobotSimulator):
    def __init__(self) -> None:
        super().__init__()
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1
        return super().stop()


def test_lifespan_creates_robot_when_none():
    backend.main.robot = None
    with TestClient(app) as client:
        assert backend.main.robot is not None
        assert client.get("/api/robot/status").status_code == 200


def test_robot_routes_return_503_before_startup():
    backend.main.robot = None
    try:
        client = TestClient(app)  # no context manager: lifespan does not run
        assert client.get("/api/robot/status").status_code == 503
    finally:
        backend.main.robot = RobotSimulator()  # conftest teardown expects a robot


def test_shutdown_stops_robot():
    spy = SpyRobot()
    backend.main.robot = spy
    with TestClient(app):
        assert backend.main.robot is spy
        before = spy.stop_calls
    assert spy.stop_calls == before + 1
