"""Intensity when the robot API has no /movement-style route (the dog's robot_api.py on :8100)."""

import json

import httpx
import pytest

from backend.robot import protocol
from backend.robot.config import RobotConfig
from backend.robot.interface import CommandStatus, Gait, Intensity, MotionCommand, MovementStyle, RobotMode
from backend.robot.remote import RemoteRobotClient
from tests.test_robot_remote import FakePi


class OldDog(FakePi):
    """Like the real dog API: no movement-style route."""

    def __call__(self, request):
        if request.url.path == protocol.PATH_MOVEMENT_STYLE:
            self.requests.append(request)
            return httpx.Response(404, json={"detail": "Not Found"})
        return super().__call__(request)


def make(pi):
    return RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE, api_key="k3y"), transport=httpx.MockTransport(pi))


def last_motion(pi):
    return json.loads(pi.by_path(protocol.PATH_MOTION)[-1].content)


def test_missing_route_falls_back_to_laptop_scaling():
    pi = OldDog(); c = make(pi)
    try:
        c.move_analog(MotionCommand(forward=1.0, strafe=0.0, turn=-0.5))
        assert last_motion(pi) == {"forward": 1.0, "strafe": 0.0, "turn": -0.5}       # untouched before any style change
        r = c.set_movement_style(MovementStyle(Intensity.LOW, Gait.TROT))
        assert r.status == CommandStatus.ACCEPTED and "laptop" in r.message
        c.move_analog(MotionCommand(forward=1.0, strafe=0.0, turn=-0.5))
        assert last_motion(pi) == pytest.approx({"forward": 0.4, "strafe": 0.0, "turn": -0.2})
        c.set_movement_style(MovementStyle(Intensity.MEDIUM, Gait.TROT))
        c.move_analog(MotionCommand(forward=1.0, strafe=0.0, turn=0.0))
        assert last_motion(pi)["forward"] == pytest.approx(0.7)
        c.set_movement_style(MovementStyle(Intensity.HIGH, Gait.TROT))
        c.move_analog(MotionCommand(forward=1.0, strafe=-1.0, turn=1.0))
        assert last_motion(pi) == {"forward": 1.0, "strafe": -1.0, "turn": 1.0}      # HIGH never exceeds the raw stick
        assert len(pi.by_path(protocol.PATH_MOVEMENT_STYLE)) == 1                   # route probed once, then remembered
    finally:
        c.close()


def test_api_with_route_is_unchanged():
    pi = FakePi(); c = make(pi)
    try:
        r = c.set_movement_style(MovementStyle(Intensity.LOW, Gait.TROT))
        assert r.status == CommandStatus.ACCEPTED and "laptop" not in r.message
        c.move_analog(MotionCommand(forward=1.0, strafe=0.0, turn=0.0))
        assert last_motion(pi)["forward"] == 1.0                                    # the robot API scales itself
    finally:
        c.close()


def test_other_errors_are_not_masked():
    pi = FakePi(); pi.motion_status = 503                                           # robot busy/offline: report it
    c = make(pi)
    try:
        r = c.set_movement_style(MovementStyle(Intensity.LOW, Gait.TROT))
        assert r.status != CommandStatus.ACCEPTED and "laptop" not in r.message
        pi.motion_status = 200
        c.move_analog(MotionCommand(forward=1.0, strafe=0.0, turn=0.0))
        assert last_motion(pi)["forward"] == 1.0                                    # no laptop scaling switched on
    finally:
        c.close()


def test_telemetry_does_not_reset_laptop_intensity():
    pi = OldDog(); c = make(pi)                     # FakePi telemetry always reports intensity "low", gait "walk"
    try:
        c.set_movement_style(MovementStyle(Intensity.HIGH, Gait.TROT))
        telemetry = c.read_telemetry()
        assert telemetry.control_state.intensity == Intensity.HIGH and telemetry.control_state.gait == Gait.TROT
        c.move_analog(MotionCommand(forward=1.0, strafe=0.0, turn=0.0))
        assert last_motion(pi)["forward"] == 1.0    # still HIGH after the telemetry read
    finally:
        c.close()
