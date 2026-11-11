from fastapi.testclient import TestClient

from backend.camera.config import CameraConfig
from backend.camera.interface import CameraUnavailableError
from backend.camera.source import FrameSource
from backend.camera.synthetic import SyntheticSource
from backend.robot.config import RobotConfig
from backend.robot.server import create_app
from tests.test_robot_server import RecordingRobot


class FailingSource(FrameSource):
    @property
    def name(self): return "broken-camera"
    def open(self): raise CameraUnavailableError("not connected")
    def read(self): raise CameraUnavailableError("not connected")
    def close(self): pass


def make_app(source, camera_config):
    config = RobotConfig(api_key="robot-key")
    robot = RecordingRobot()
    return robot, create_app(robot, config, camera_config=camera_config, camera_source=source)


def test_pi_camera_routes_use_shared_synthetic_camera_and_robot_key():
    camera_config = CameraConfig(device="synthetic", width=160, height=120, target_fps=15)
    robot, app = make_app(SyntheticSource(camera_config), camera_config)
    with TestClient(app) as client:
        status = client.get("/api/v1/camera/status", headers={"X-Robot-Key": "robot-key"})
        assert status.status_code == 200
        assert status.json()["connected"] is True
        assert client.get("/api/v1/camera/status", headers={"X-Camera-Key": "robot-key"}).status_code == 401
        assert client.post("/api/v1/stop", headers={"X-Robot-Key": "robot-key"}).status_code == 200


def test_camera_open_failure_reports_unavailable_without_blocking_stop_or_estop():
    camera_config = CameraConfig(device=0, width=160, height=120, target_fps=15)
    robot, app = make_app(FailingSource(), camera_config)
    with TestClient(app) as client:
        status = client.get("/api/v1/camera/status", headers={"X-Robot-Key": "robot-key"})
        assert status.status_code == 200
        assert status.json()["connected"] is False
        assert client.post("/api/v1/stop", headers={"X-Robot-Key": "robot-key"}).status_code == 200
        assert client.post("/api/v1/emergency-stop", headers={"X-Robot-Key": "robot-key"}).status_code == 200
        assert robot.calls[-2:] == ["stop", "emergency_stop"]


def test_invalid_camera_configuration_does_not_prevent_robot_safety_routes(monkeypatch):
    monkeypatch.setenv("CAMERA_WIDTH", "not-a-width")
    robot = RecordingRobot()
    app = create_app(robot, RobotConfig(api_key="robot-key"))
    with TestClient(app) as client:
        assert client.post("/api/v1/stop", headers={"X-Robot-Key": "robot-key"}).status_code == 200
        assert client.post("/api/v1/emergency-stop", headers={"X-Robot-Key": "robot-key"}).status_code == 200
        assert robot.calls[-2:] == ["stop", "emergency_stop"]
