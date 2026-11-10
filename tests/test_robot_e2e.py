"""End-to-end: laptop ``RemoteRobotClient`` -> Pi ``server`` -> ``RobotSimulator``.

Runs the real HTTP stack in-process (FastAPI ``TestClient`` transport), so the
wire contract in ``backend/robot/protocol.py`` is exercised from both sides.
"""

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.robot.config import RobotConfig
from backend.robot.interface import CommandStatus, Intensity, MotionCommand, MovementStyle, Posture
from backend.robot.remote import RemoteRobotClient
from backend.robot.server import create_app
from backend.robot.simulator import RobotSimulator


class _TestClientTransport(httpx.BaseTransport):
    """Bridges the client's ``httpx`` to a Starlette ``TestClient`` via its public API.

    ``TestClient._transport`` is built on whichever httpx flavour Starlette picked (it switches
    to ``httpx2`` when that is installed), which the ``httpx``-based RemoteRobotClient rejects.
    Going through ``TestClient.request`` works with either.
    """

    def __init__(self, tc: TestClient) -> None:
        self._tc = tc

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._tc.request(
            request.method,
            str(request.url),
            content=request.read(),
            headers=dict(request.headers),
        )
        return httpx.Response(
            response.status_code,
            headers=[(k, v) for k, v in response.headers.items() if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")],
            content=response.content,
            request=request,
        )


@pytest.fixture
def stack():
    """Yields ``(sim, operator_a, operator_b)`` sharing one in-process Pi server."""
    cfg = RobotConfig(api_key="test-key", watchdog_ms=200, remote_url="http://testserver")
    sim = RobotSimulator(cfg)
    with TestClient(create_app(sim, cfg)) as tc:
        a = RemoteRobotClient(cfg, transport=_TestClientTransport(tc))
        b = RemoteRobotClient(cfg, transport=_TestClientTransport(tc))
        yield sim, a, b
        a.close()
        b.close()


def test_motion_reaches_simulator(stack):
    sim, a, _ = stack
    assert a.move_analog(MotionCommand(forward=0.8)).status == CommandStatus.ACCEPTED
    assert sim.moving and sim.x > 0


def test_single_operator_but_anyone_can_stop(stack):
    sim, a, b = stack
    a.set_movement_style(MovementStyle("high", "trot"))
    a.move_analog(MotionCommand(forward=0.8))

    assert b.move_analog(MotionCommand(forward=0.5)).status == CommandStatus.REJECTED

    res = b.stop()
    assert res.status == CommandStatus.STOP_DISPATCHED
    assert res.intensity == Intensity.HIGH  # stop preserves intensity
    assert not sim.moving


def test_watchdog_stops_when_commands_cease(stack):
    sim, a, _ = stack
    a.move_analog(MotionCommand(forward=0.8))
    deadline = time.monotonic() + 2.0
    while sim.moving and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not sim.moving


def test_safety_rejections_propagate(stack):
    _, a, _ = stack
    assert a.set_posture(Posture("medium", roll=30)).status == CommandStatus.REJECTED
    assert a.perform_action(99).status == CommandStatus.REJECTED


def test_emergency_stop_latch_round_trip(stack):
    sim, a, _ = stack
    a.move_analog(MotionCommand(forward=0.5))

    assert a.emergency_stop()["success"] is True
    assert sim.emergency_stopped and not sim.moving
    assert a.move_analog(MotionCommand(forward=0.5)).status == CommandStatus.REJECTED

    assert a.reset_emergency_stop()["success"] is True
    assert a.move_analog(MotionCommand(forward=0.5)).status == CommandStatus.ACCEPTED


def test_telemetry_and_close_stops(stack):
    sim, a, _ = stack
    a.move_analog(MotionCommand(forward=0.5))
    telemetry = a.read_telemetry()
    assert telemetry.battery is not None
    assert telemetry.control_state.moving is True

    a.close()
    assert not sim.moving  # releasing the session stops the robot
