"""Tests for the v1 RobotInterface implementation in RobotSimulator."""

import math

import pytest

from backend.robot.config import APPROVED_ACTIONS, RobotConfig
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
from backend.robot.simulator import RobotSimulator


@pytest.fixture
def sim():
    return RobotSimulator()


def test_identity(sim):
    assert sim.robot_mode == RobotMode.SIMULATOR
    assert sim.connected is True


# --- analog motion ----------------------------------------------------


def test_dead_zone_ignores_small_input(sim):
    res = sim.move_analog(MotionCommand(forward=0.05, strafe=-0.1, turn=0.1))
    assert res.ok
    assert (sim.x, sim.y, sim.heading) == (0.0, 0.0, 0.0)
    assert sim.get_control_state().moving is False


def test_zero_command_is_stop(sim):
    sim.move_analog(MotionCommand(forward=1.0))
    assert sim.get_control_state().moving is True
    sim.move_analog(MotionCommand())
    assert sim.get_control_state().moving is False
    assert sim.last_command == "stop"


def test_forward_scaled_by_intensity_and_max_scale(sim):
    sim.move_analog(MotionCommand(forward=1.0))
    # medium 0.7 * max_motion_scale 0.6 = 0.42 m at heading 0
    assert sim.x == pytest.approx(0.42, abs=1e-3)
    assert sim.y == 0.0
    assert sim.last_sdk_command["move_x"] == round(0.42 * 20)


def test_high_intensity_never_exceeds_max_scale():
    sim = RobotSimulator(RobotConfig(max_motion_scale=0.5))
    sim.set_movement_style(MovementStyle(Intensity.HIGH, Gait.TROT))
    sim.move_analog(MotionCommand(forward=1.0, strafe=1.0, turn=1.0))
    assert abs(sim.last_sdk_command["move_x"]) <= 0.5 * 20
    assert abs(sim.last_sdk_command["move_y"]) <= 0.5 * 18


def test_backward_and_strafe_and_turn_directions(sim):
    sim.move_analog(MotionCommand(forward=-1.0))
    assert sim.x < 0
    sim = RobotSimulator()
    sim.move_analog(MotionCommand(strafe=1.0))  # left of heading 0 => +y
    assert sim.y > 0 and sim.x == pytest.approx(0.0, abs=1e-6)
    sim = RobotSimulator()
    sim.move_analog(MotionCommand(turn=1.0))  # left => heading increases
    assert 0 < sim.heading < 180
    sim = RobotSimulator()
    sim.move_analog(MotionCommand(turn=-1.0))
    assert sim.heading > 180


def test_forward_follows_heading():
    sim = RobotSimulator()
    sim.heading = 90.0
    sim.move_analog(MotionCommand(forward=1.0))
    assert sim.x == pytest.approx(0.0, abs=1e-3)
    assert sim.y > 0


def test_sdk_turn_minimum_nonzero():
    sim = RobotSimulator(RobotConfig(dead_zone=0.0))
    sim.set_movement_style(MovementStyle(Intensity.LOW, Gait.TROT))
    sim.move_analog(MotionCommand(turn=0.2))
    assert abs(sim.last_sdk_command["turn"]) >= 30


def test_invalid_motion_values_rejected_by_value_object():
    with pytest.raises(ValueError):
        MotionCommand(forward=1.5)
    with pytest.raises(ValueError):
        MotionCommand(turn=float("nan"))


# --- discrete motion --------------------------------------------------


@pytest.mark.parametrize("direction", list(Direction))
def test_all_discrete_directions_accepted(sim, direction):
    res = sim.move_discrete(direction)
    assert res.status == CommandStatus.ACCEPTED
    assert sim.get_control_state().moving is True


def test_discrete_geometry(sim):
    sim.move_discrete(Direction.FORWARD)
    assert sim.x > 0
    sim.stop()
    h = sim.heading
    sim.move_discrete(Direction.TURN_LEFT)
    assert sim.heading != h
    sim.move_discrete(Direction.RIGHT)
    assert sim.last_sdk_command["move_y"] < 0


def test_discrete_intensity_override_does_not_change_style(sim):
    sim.move_discrete(Direction.FORWARD, Intensity.HIGH)
    assert sim.last_sdk_command["move_x"] == 14
    assert sim.get_control_state().intensity == Intensity.MEDIUM
    sim.stop()
    sim.move_discrete(Direction.FORWARD)
    assert sim.last_sdk_command["move_x"] == 10


# --- stop -------------------------------------------------------------


def test_stop_preserves_intensity_and_gait(sim):
    sim.set_movement_style(MovementStyle(Intensity.HIGH, Gait.WALK))
    sim.move_analog(MotionCommand(forward=1.0))
    res = sim.stop()
    assert res.status == CommandStatus.STOP_DISPATCHED
    assert res.intensity == Intensity.HIGH
    state = sim.get_control_state()
    assert (state.intensity, state.gait, state.moving) == (Intensity.HIGH, Gait.WALK, False)


def test_stop_legacy_dict_access(sim):
    assert sim.stop()["success"] is True


# --- style & posture --------------------------------------------------


def test_style_does_not_change_posture(sim):
    sim.set_posture(Posture(HeightPreset.HIGH, roll=5))
    sim.set_movement_style(MovementStyle(Intensity.LOW, Gait.WALK))
    assert sim.get_control_state().posture == Posture(HeightPreset.HIGH, roll=5)


def test_posture_does_not_change_style(sim):
    sim.set_movement_style(MovementStyle(Intensity.LOW, Gait.WALK))
    sim.set_posture(Posture(HeightPreset.LOW))
    state = sim.get_control_state()
    assert (state.intensity, state.gait) == (Intensity.LOW, Gait.WALK)


@pytest.mark.parametrize(
    "posture",
    [Posture(roll=16), Posture(pitch=-11), Posture(yaw=9)],
)
def test_posture_rejects_out_of_limit_angles(sim, posture):
    res = sim.set_posture(posture)
    assert res.status == CommandStatus.REJECTED
    assert sim.get_control_state().posture == Posture()


def test_posture_accepts_limit_values(sim):
    assert sim.set_posture(Posture(roll=15, pitch=-10, yaw=8)).ok


def test_unsafe_style_rejected_and_unchanged(sim):
    res = sim.set_movement_style(MovementStyle(Intensity.HIGH, Gait.HIGH_WALK))
    assert res.status == CommandStatus.REJECTED
    state = sim.get_control_state()
    assert (state.intensity, state.gait) == (Intensity.MEDIUM, Gait.TROT)


def test_unsafe_posture_for_current_gait_rejected(sim):
    sim.set_movement_style(MovementStyle(Intensity.MEDIUM, Gait.HIGH_WALK))
    res = sim.set_posture(Posture(HeightPreset.LOW))
    assert res.status == CommandStatus.REJECTED
    assert sim.get_control_state().posture.height == HeightPreset.MEDIUM


# --- actions & reset --------------------------------------------------


def test_list_actions_is_allowlist(sim):
    actions = {a.id: a.name for a in sim.list_actions()}
    assert actions == dict(APPROVED_ACTIONS)
    assert 255 not in actions


def test_approved_action_runs_when_idle(sim):
    assert sim.perform_action(16).status == CommandStatus.ACCEPTED


@pytest.mark.parametrize("bad", [0, 12, 255, -1, True, "16", 16.0])
def test_unknown_actions_rejected(sim, bad):
    assert sim.perform_action(bad).status == CommandStatus.REJECTED


def test_action_rejected_while_moving(sim):
    sim.move_analog(MotionCommand(forward=1.0))
    assert sim.perform_action(16).status == CommandStatus.REJECTED
    sim.stop()
    assert sim.perform_action(16).ok


def test_reset_restores_neutral_posture_keeps_style(sim):
    sim.set_movement_style(MovementStyle(Intensity.LOW, Gait.WALK))
    sim.set_posture(Posture(HeightPreset.HIGH, roll=5, pitch=3, yaw=2))
    sim.move_analog(MotionCommand(forward=1.0))
    assert sim.reset().ok
    state = sim.get_control_state()
    assert state.posture == Posture()
    assert state.moving is False
    assert (state.intensity, state.gait) == (Intensity.LOW, Gait.WALK)


# --- e-stop latch -----------------------------------------------------


def test_estop_blocks_everything_but_stop(sim):
    sim.emergency_stop()
    assert sim.move_analog(MotionCommand(forward=1.0)).status == CommandStatus.REJECTED
    assert sim.move_discrete(Direction.FORWARD).status == CommandStatus.REJECTED
    assert sim.set_posture(Posture()).status == CommandStatus.REJECTED
    assert sim.set_movement_style(MovementStyle()).status == CommandStatus.REJECTED
    assert sim.perform_action(16).status == CommandStatus.REJECTED
    assert sim.reset().status == CommandStatus.REJECTED
    assert sim.stop().status == CommandStatus.STOP_DISPATCHED
    assert sim.get_control_state().emergency_stopped is True

    sim.reset_emergency_stop()
    assert sim.move_analog(MotionCommand(forward=1.0)).ok


# --- telemetry --------------------------------------------------------


def test_telemetry_shape_and_separation(sim):
    sim.set_posture(Posture(roll=4, pitch=2))
    t = sim.read_telemetry()
    data = t.to_dict()
    assert set(data) == {"battery", "orientation", "firmware", "read_errors", "control_state"}
    assert data["battery"] == sim.battery_level
    assert data["orientation"]["roll"] == 4
    assert data["read_errors"] == []
    assert data["firmware"]
    assert data["control_state"]["intensity"] == "medium"
    assert data["control_state"]["moving"] is False


def test_control_state_is_a_copy(sim):
    state = sim.get_control_state()
    state.intensity = Intensity.HIGH
    assert sim.get_control_state().intensity == Intensity.MEDIUM


def test_battery_depletion_rejects_motion():
    sim = RobotSimulator(battery_level=0.05)
    assert sim.move_analog(MotionCommand(forward=1.0)).ok
    assert sim.battery_level == 0.0
    assert sim.move_analog(MotionCommand(forward=1.0)).status == CommandStatus.REJECTED


def test_disconnected_motion_fails(sim):
    sim.connected = False
    assert sim.move_analog(MotionCommand(forward=1.0)).status == CommandStatus.FAILED
    assert sim.stop().status == CommandStatus.STOP_DISPATCHED
