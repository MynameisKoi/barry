"""Tests for RemoteRobotClient and the robot factory (httpx.MockTransport, no network)."""

import json

import httpx
import pytest

from backend.robot import protocol
from backend.robot.config import RobotConfig
from backend.robot.factory import create_robot
from backend.robot.interface import (
    CommandStatus,
    Direction,
    Gait,
    HeightPreset,
    Intensity,
    MotionCommand,
    MovementStyle,
    Posture,
    RobotMode,
)
from backend.robot.remote import RemoteRobotClient
from backend.robot.simulator import RobotSimulator


class FakePi:
    """Minimal in-memory stand-in for the Pi Robot API."""

    def __init__(self):
        self.requests = []
        self.session_counter = 0
        self.valid_session = None
        self.session_status = 200
        self.fail = None  # exception class to raise for every request
        self.motion_status = 200
        self.motion_409_once = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise self.fail("boom", request=request)
        path, method = request.url.path, request.method
        if path == protocol.PATH_SESSION and method == "POST":
            if self.session_status != 200:
                return httpx.Response(self.session_status, json={"detail": "held"})
            self.session_counter += 1
            self.valid_session = f"s{self.session_counter}"
            return httpx.Response(200, json={"session_id": self.valid_session, "ttl_s": 10.0})
        if path == protocol.PATH_SESSION and method == "DELETE":
            self.valid_session = None
            return httpx.Response(200, json={})
        if path == protocol.PATH_STOP:
            return httpx.Response(200, json={"status": "stop_dispatched", "command": "stop", "intensity": "high"})
        if path == protocol.PATH_EMERGENCY_STOP:
            return httpx.Response(200, json={"status": "stop_dispatched", "command": "emergency_stop"})
        if path == protocol.PATH_ACTIONS and method == "GET":
            return httpx.Response(200, json={"actions": [{"id": 16, "name": "swing"}]})
        if path == protocol.PATH_TELEMETRY:
            return httpx.Response(
                200,
                json={
                    "battery": 85,
                    "orientation": {"roll": 1.0, "pitch": None, "yaw": 3.0},
                    "firmware": "v2",
                    "read_errors": ["pitch: timeout"],
                    "control_state": {
                        "intensity": "low",
                        "gait": "walk",
                        "posture": {"height": "high", "roll": 2, "pitch": 0, "yaw": 0},
                        "moving": True,
                        "emergency_stopped": False,
                        "last_command": "motion",
                    },
                },
            )
        # mutating commands
        if self.motion_409_once:
            self.motion_409_once = False
            return httpx.Response(409, json={"detail": "session expired"})
        if self.motion_status != 200:
            return httpx.Response(self.motion_status, json={"detail": "nope"})
        if request.headers.get(protocol.SESSION_HEADER) != self.valid_session:
            return httpx.Response(409, json={"detail": "bad session"})
        return httpx.Response(200, json={"status": "accepted", "command": path.rsplit("/", 1)[-1]})

    def by_path(self, path):
        return [r for r in self.requests if r.url.path == path]


@pytest.fixture
def pi():
    return FakePi()


@pytest.fixture
def client(pi):
    c = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE, api_key="k3y"), transport=httpx.MockTransport(pi))
    yield c
    c.close()


# --- headers, session, seq ----------------------------------------------


def test_identity(client):
    assert client.robot_mode == RobotMode.REMOTE
    assert client.connected is True


def test_api_key_and_session_headers_sent(client, pi):
    res = client.move_analog(MotionCommand(forward=0.5))
    assert res.status == CommandStatus.ACCEPTED
    assert all(r.headers[protocol.AUTH_HEADER] == "k3y" for r in pi.requests)
    motion = pi.by_path(protocol.PATH_MOTION)[0]
    assert motion.headers[protocol.SESSION_HEADER] == "s1"
    assert protocol.SENT_AT_HEADER in motion.headers
    assert json.loads(motion.content) == {"forward": 0.5, "strafe": 0.0, "turn": 0.0}


def test_session_acquired_lazily_and_once(client, pi):
    assert pi.by_path(protocol.PATH_SESSION) == []
    client.move_analog(MotionCommand(forward=0.5))
    client.move_analog(MotionCommand(forward=0.6))
    assert len(pi.by_path(protocol.PATH_SESSION)) == 1


def test_seq_strictly_increases(client, pi):
    client.move_analog(MotionCommand(forward=0.5))
    client.move_discrete(Direction.LEFT, Intensity.HIGH)
    client.set_movement_style(MovementStyle(Intensity.LOW, Gait.WALK))
    client.reset()
    seqs = [int(r.headers[protocol.SEQ_HEADER]) for r in pi.requests if protocol.SEQ_HEADER in r.headers]
    assert len(seqs) == 4
    assert seqs == sorted(set(seqs))


def test_session_reacquired_once_on_409(client, pi):
    client.move_analog(MotionCommand(forward=0.5))
    pi.motion_409_once = True
    res = client.move_analog(MotionCommand(forward=0.5))
    assert res.status == CommandStatus.ACCEPTED
    assert len(pi.by_path(protocol.PATH_SESSION)) == 2
    last = pi.by_path(protocol.PATH_MOTION)[-1]
    assert last.headers[protocol.SESSION_HEADER] == "s2"
    seqs = [int(r.headers[protocol.SEQ_HEADER]) for r in pi.by_path(protocol.PATH_MOTION)]
    assert seqs == sorted(set(seqs))


def test_second_409_is_rejected_not_looped(client, pi):
    pi.motion_status = 409
    res = client.move_analog(MotionCommand(forward=0.5))
    assert res.status == CommandStatus.REJECTED
    assert len(pi.by_path(protocol.PATH_MOTION)) == 2  # original + one retry


def test_session_held_by_other_operator_is_rejected(client, pi):
    pi.session_status = 409
    res = client.move_analog(MotionCommand(forward=0.5))
    assert res.status == CommandStatus.REJECTED
    assert pi.by_path(protocol.PATH_MOTION) == []


# --- body mapping --------------------------------------------------------


def test_request_bodies(client, pi):
    client.move_discrete(Direction.TURN_LEFT)
    client.set_movement_style(MovementStyle(Intensity.LOW, Gait.WALK))
    client.set_posture(Posture(HeightPreset.HIGH, roll=1, pitch=2, yaw=3))
    client.perform_action(16)
    assert json.loads(pi.by_path(protocol.PATH_MOTION_DISCRETE)[0].content) == {"direction": "turn_left"}
    assert json.loads(pi.by_path(protocol.PATH_MOVEMENT_STYLE)[0].content) == {"intensity": "low", "gait": "walk"}
    assert json.loads(pi.by_path(protocol.PATH_POSTURE)[0].content) == {
        "height": "high", "roll": 1.0, "pitch": 2.0, "yaw": 3.0
    }
    assert len(pi.by_path("/api/v1/actions/16")) == 1


def test_bad_action_id_rejected_locally(client, pi):
    assert client.perform_action(True).status == CommandStatus.REJECTED
    assert pi.requests == []


# --- stop priority -------------------------------------------------------


def test_stop_without_session_or_seq(client, pi):
    res = client.stop()
    assert res.status == CommandStatus.STOP_DISPATCHED
    assert res.intensity == Intensity.HIGH
    assert pi.by_path(protocol.PATH_SESSION) == []
    req = pi.by_path(protocol.PATH_STOP)[0]
    assert protocol.SESSION_HEADER not in req.headers
    assert protocol.SEQ_HEADER not in req.headers
    assert req.headers[protocol.AUTH_HEADER] == "k3y"


def test_stop_not_blocked_by_held_command_lock(client, pi):
    with client._cmd_lock:  # simulates a slow in-flight motion request
        assert client.stop().ok
        assert client.emergency_stop()["success"] is True


def test_emergency_stop_without_session(client, pi):
    res = client.emergency_stop()
    assert res["success"] is True
    assert pi.by_path(protocol.PATH_SESSION) == []
    assert client.get_control_state().emergency_stopped is True


# --- link loss never raises ---------------------------------------------


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout])
def test_link_loss_returns_failed(client, pi, exc):
    pi.fail = exc
    assert client.move_analog(MotionCommand(forward=0.5)).status == CommandStatus.FAILED
    assert client.stop().status == CommandStatus.FAILED
    assert client.emergency_stop()["success"] is False
    assert client.list_actions() == []
    tele = client.read_telemetry()
    assert tele.battery is None and tele.read_errors
    assert client.connected is False
    assert client.get_status()["connected"] is False
    assert client.move("forward", 0.5)["success"] is False


def test_link_recovers(client, pi):
    pi.fail = httpx.ConnectError
    client.stop()
    assert client.connected is False
    pi.fail = None
    client.stop()
    assert client.connected is True


def test_503_marks_disconnected(client, pi):
    pi.motion_status = 503
    assert client.move_analog(MotionCommand(forward=0.5)).status == CommandStatus.FAILED
    assert client.connected is False


@pytest.mark.parametrize("code,status", [(401, CommandStatus.REJECTED), (422, CommandStatus.REJECTED),
                                         (423, CommandStatus.REJECTED), (500, CommandStatus.FAILED)])
def test_error_codes_mapped(client, pi, code, status):
    pi.motion_status = code
    res = client.move_analog(MotionCommand(forward=0.5))
    assert res.status == status
    assert str(code) in res.message


# --- response mapping ----------------------------------------------------


def test_telemetry_mapping(client):
    t = client.read_telemetry()
    assert t.battery == 85
    assert (t.orientation.roll, t.orientation.pitch, t.orientation.yaw) == (1.0, None, 3.0)
    assert t.firmware == "v2"
    assert t.read_errors == ["pitch: timeout"]
    cs = t.control_state
    assert (cs.intensity, cs.gait, cs.moving) == (Intensity.LOW, Gait.WALK, True)
    assert cs.posture == Posture(HeightPreset.HIGH, roll=2)
    assert client.get_control_state().intensity == Intensity.LOW


def test_actions_mapping(client):
    actions = client.list_actions()
    assert [(a.id, a.name) for a in actions] == [(16, "swing")]


def test_control_state_mirrors_accepted_commands(client):
    client.set_movement_style(MovementStyle(Intensity.HIGH, Gait.WALK))
    client.move_analog(MotionCommand(forward=1.0))
    s = client.get_control_state()
    assert (s.intensity, s.gait, s.moving) == (Intensity.HIGH, Gait.WALK, True)
    client.stop()
    assert client.get_control_state().moving is False
    assert client.get_control_state().intensity == Intensity.HIGH


# --- legacy API ----------------------------------------------------------


def test_legacy_move_maps_to_analog(client, pi):
    assert client.move("forward", 0.5)["success"] is True
    assert client.move("left", 0.4)["success"] is True
    bodies = [json.loads(r.content) for r in pi.by_path(protocol.PATH_MOTION)]
    assert bodies[0] == {"forward": 0.5, "strafe": 0.0, "turn": 0.0}
    assert bodies[1] == {"forward": 0.0, "strafe": 0.0, "turn": 0.4}


def test_legacy_move_validation(client, pi):
    assert client.move("jump")["success"] is False
    assert client.move(None)["success"] is False  # type: ignore[arg-type]
    assert client.move("forward", float("nan"))["success"] is False
    assert client.move("forward", "fast")["success"] is False  # type: ignore[arg-type]
    assert pi.requests == []


def test_legacy_stop_and_status_shape(client):
    assert client.move("stop")["success"] is True
    status = client.get_status()
    assert {"connected", "mode", "battery_level", "position", "speed",
            "last_command", "emergency_stopped", "timestamp"} <= set(status)
    assert status["mode"] == "remote"
    assert status["battery_level"] == 85.0
    assert status["connected"] is True


def test_legacy_reset_emergency_stop(client, pi):
    client.emergency_stop()
    assert client.reset_emergency_stop()["success"] is True
    assert client.get_control_state().emergency_stopped is False
    assert len(pi.by_path(protocol.PATH_EMERGENCY_RESET)) == 1


# --- lifecycle -----------------------------------------------------------


def test_close_releases_session(pi):
    c = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE), transport=httpx.MockTransport(pi))
    c.move_analog(MotionCommand(forward=0.5))
    c.close()
    c.close()  # idempotent
    deletes = [r for r in pi.requests if r.method == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0].headers[protocol.SESSION_HEADER] == "s1"
    assert pi.valid_session is None


def test_close_without_session_sends_nothing(pi):
    c = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE), transport=httpx.MockTransport(pi))
    c.close()
    assert pi.requests == []


def test_no_api_key_header_when_unset(pi):
    c = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE), transport=httpx.MockTransport(pi))
    c.stop()
    assert protocol.AUTH_HEADER not in pi.requests[0].headers


# --- factory -------------------------------------------------------------


def test_factory_simulator():
    robot = create_robot(RobotConfig(mode=RobotMode.SIMULATOR))
    assert isinstance(robot, RobotSimulator)
    assert robot.robot_mode == RobotMode.SIMULATOR


def test_factory_remote():
    robot = create_robot(RobotConfig(mode=RobotMode.REMOTE, remote_url="http://pi:8100"))
    assert isinstance(robot, RemoteRobotClient)
    robot.close()


def test_factory_dogzilla_uses_controller(monkeypatch):
    """Stubs the controller: it is implemented by a different task and needs hardware."""
    import backend.robot.dogzilla as dogzilla

    created = {}

    class FakeController:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(dogzilla, "DogzillaController", FakeController)
    robot = create_robot(RobotConfig(mode=RobotMode.DOGZILLA, dogzilla_port="/dev/ttyTEST"))
    assert isinstance(robot, FakeController)
    assert created == {"device_endpoint": "/dev/ttyTEST"}


def test_factory_default_reads_env(monkeypatch):
    monkeypatch.setenv("ROBOT_MODE", "simulator")
    assert isinstance(create_robot(), RobotSimulator)
