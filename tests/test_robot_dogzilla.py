"""Hardware-free contract tests for the DOGZILLA adapter."""
import threading
import time

import pytest

from backend.robot.config import RobotConfig
from backend.robot.dogzilla import DogzillaController
from backend.robot.interface import (
    CommandStatus, Direction, Gait, HeightPreset, Intensity, MotionCommand,
    MovementStyle, Posture,
)
from backend.robot.simulator import RobotSimulator


class FakeDOGZILLA:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.overlap = False
        self.fail_read = set()
        self.fail_once = set()

    def _record(self, name, *args):
        self.active += 1
        if self.active > 1:
            self.overlap = True
        time.sleep(0.001)
        self.calls.append((name, *args))
        self.active -= 1
        if name in self.fail_once:
            self.fail_once.remove(name)
            raise OSError(f"{name} failed")

    def __getattr__(self, name):
        if name.startswith("read_"):
            def read():
                self._record(name)
                if name in self.fail_read:
                    raise OSError("read failed")
                return {
                    "read_battery": 87, "read_roll": 1.0, "read_pitch": 2.0,
                    "read_yaw": 3.0, "read_version": "2.0.7",
                }[name]
            return read
        return lambda *args: self._record(name, *args)


def test_analog_scaling_dead_zone_and_turn_quirk():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(RobotConfig(dead_zone=0.1, max_motion_scale=1.0), sdk)
    result = robot.move_analog(MotionCommand(forward=1, strafe=-1, turn=0.5))
    assert result.status is CommandStatus.ACCEPTED
    assert sdk.calls == [("move_x", 14), ("move_y", -13), ("turn", 0)]


def test_discrete_mapping_and_override_does_not_change_style():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    robot.move_discrete(Direction.TURN_RIGHT, Intensity.HIGH)
    assert sdk.calls == [("turnright", 50)]
    assert robot.get_control_state().intensity is Intensity.MEDIUM


def test_stop_preserves_intensity_and_never_sets_pace():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    robot.set_movement_style(MovementStyle(Intensity.HIGH, Gait.TROT))
    sdk.calls.clear()
    result = robot.stop()
    assert sdk.calls == [("stop",)]
    assert result.intensity is Intensity.HIGH


def test_style_does_not_touch_height_and_posture_maps_axes():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    robot.set_movement_style(MovementStyle(Intensity.LOW, Gait.WALK))
    assert sdk.calls == [("pace", "slow"), ("gait_type", "walk")]
    sdk.calls.clear()
    robot.set_posture(Posture(HeightPreset.HIGH, 1, 2, 3))
    assert sdk.calls == [
        ("translation", "z", 105), ("attitude", "r", 1.0),
        ("attitude", "p", 2.0), ("attitude", "y", 3.0),
    ]


def test_action_allowlist_and_reset_reapplies_style():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    assert robot.perform_action(99).status is CommandStatus.REJECTED
    assert sdk.calls == []
    assert robot.perform_action(10).ok
    assert sdk.calls == [("action", 10)]
    sdk.calls.clear()
    assert robot.reset().ok
    assert sdk.calls == [("reset",), ("pace", "normal"), ("gait_type", "trot")]


def test_telemetry_failures_are_none_not_fabricated():
    sdk = FakeDOGZILLA()
    sdk.fail_read = {"read_pitch", "read_version"}
    telemetry = DogzillaController(sdk=sdk).read_telemetry()
    assert telemetry.battery == 87
    assert telemetry.orientation.pitch is None
    assert telemetry.firmware is None
    assert any(error.startswith("pitch:") for error in telemetry.read_errors)
    assert any(error.startswith("firmware:") for error in telemetry.read_errors)


def test_import_failure_is_disconnected(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fail(name, *args, **kwargs):
        if name == "DOGZILLALib":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail)
    robot = DogzillaController()
    assert robot.connected is False
    assert robot.move_discrete(Direction.FORWARD).status is CommandStatus.FAILED


def test_concurrent_sdk_calls_are_serialized():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    threads = [
        threading.Thread(target=robot.move_discrete, args=(Direction.FORWARD,))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sdk.overlap is False
    assert len(sdk.calls) == 8


def test_estop_latches_and_stop_remains_allowed():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    assert robot.emergency_stop()["success"]
    assert robot.move_discrete(Direction.FORWARD).status is CommandStatus.REJECTED
    assert robot.stop().status is CommandStatus.STOP_DISPATCHED
    robot.reset_emergency_stop()
    assert robot.move_discrete(Direction.FORWARD).ok


def test_partial_motion_failure_best_effort_stops_and_can_reconnect():
    """A second-axis failure must stop motion; a later stop retries the SDK."""
    sdk = FakeDOGZILLA()
    sdk.fail_once.add("move_y")
    robot = DogzillaController(
        RobotConfig(dead_zone=0.1, max_motion_scale=1.0), sdk
    )

    result = robot.move_analog(MotionCommand(0.8, 0.5, 0))

    assert result.status is CommandStatus.FAILED
    assert sdk.calls[-1] == ("stop",)
    assert robot.connected is False

    sdk.calls.clear()
    assert robot.stop().status is CommandStatus.STOP_DISPATCHED
    assert sdk.calls == [("stop",)]
    assert robot.connected is True


def test_legacy_left_and_right_turn_instead_of_strafe():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    assert robot.move("left", 0.5)["success"]
    assert robot.stop().ok
    assert robot.move("right", 0.5)["success"]
    assert ("turnleft", 40) in sdk.calls
    assert ("turnright", 40) in sdk.calls
    assert not any(call[0] in {"left", "right"} for call in sdk.calls)


def _names(sdk):
    return [c[0] for c in sdk.calls]


def test_legacy_posture_presets_work_and_update_state():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    assert robot.set_posture(preset="crouch")["success"]
    assert ("translation", "z", 85) in sdk.calls
    state = robot.get_control_state()
    assert state.posture.height is HeightPreset.LOW and state.intensity is Intensity.LOW
    assert robot.set_posture(preset="stand")["success"]
    assert ("reset",) in sdk.calls
    state = robot.get_control_state()
    assert state.posture.height is HeightPreset.MEDIUM and state.gait is Gait.TROT
    assert robot.set_posture(height_z=500)["success"]
    assert ("translation", "z", 105) in sdk.calls
    assert robot.get_control_state().posture.height is HeightPreset.HIGH


@pytest.mark.parametrize(
    "preset", ["obstacle_crossing", "crouch", "stand", "reset", "default"]
)
def test_legacy_posture_presets_match_simulator(preset):
    hardware = DogzillaController(sdk=FakeDOGZILLA())
    simulator = RobotSimulator()

    assert hardware.set_posture(preset=preset)["success"]
    assert simulator.set_posture(preset=preset)["success"]

    hardware_state = hardware.get_control_state()
    simulator_state = simulator.get_control_state()
    assert simulator_state.posture == hardware_state.posture
    assert simulator_state.gait is hardware_state.gait
    assert simulator_state.intensity is hardware_state.intensity


def test_legacy_posture_validates_before_sending_anything():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    out = robot.set_posture(height_z=85, roll=99)
    assert not out["success"]
    out = robot.set_posture(preset="stand", pace="bogus")
    assert not out["success"]
    # unsafe combination: high_walk at high pace
    out = robot.set_posture(gait_type="high_walk", pace="high")
    assert not out["success"]
    assert sdk.calls == [c for c in sdk.calls if c[0].startswith("read_")]
    assert robot.get_control_state().posture == Posture()


def test_legacy_posture_updates_state_and_motion_checks_use_it():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    assert robot.set_posture(gait_type="high_walk", pace="slow")["success"]
    state = robot.get_control_state()
    assert state.gait is Gait.HIGH_WALK and state.intensity is Intensity.LOW
    # now height low + high_walk is unsafe and must be rejected
    assert not robot.set_posture(height_z=85)["success"]
    robot.reset()
    assert robot.get_control_state().gait is Gait.TROT or sdk.calls[-1][0] == "gait_type"


def test_estop_issues_no_motion_and_unloads():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    robot.emergency_stop()
    names = _names(sdk)
    assert "reset" not in names
    assert "unload_allmotor" in names
    assert names.index("stop") < names.index("unload_allmotor")


def test_reset_estop_does_not_move_and_failure_keeps_latch(caplog):
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    robot.emergency_stop()
    sdk.calls.clear()
    assert robot.reset_emergency_stop()["success"]
    assert [n for n in _names(sdk) if not n.startswith("read_")] == ["load_allmotor"]
    assert not robot.emergency_stopped
    robot.emergency_stop()
    sdk.fail_once.add("load_allmotor")
    out = robot.reset_emergency_stop()
    assert not out["success"] and robot.emergency_stopped
    assert "load_allmotor failed" in caplog.text


def test_estop_logs_unload_failure(caplog):
    sdk = FakeDOGZILLA()
    sdk.fail_once.add("unload_allmotor")
    robot = DogzillaController(sdk=sdk)
    assert robot.emergency_stop()["message"]
    assert robot.emergency_stopped
    assert "unload_allmotor failed" in caplog.text


def test_get_status_uses_cached_sdk_telemetry():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    status = robot.get_status()
    assert status["battery_level"] == 87.0
    assert (status["imu"]["roll"], status["imu"]["pitch"], status["imu"]["yaw"]) == (1.0, 2.0, 3.0)


def test_imu_toggle_reflected_in_status():
    sdk = FakeDOGZILLA()
    robot = DogzillaController(sdk=sdk)
    assert robot.set_posture(imu_enabled=False)["telemetry"]["imu"]["imu_stabilization"] is False
    assert ("imu", 0) in sdk.calls


def test_max_speed_zero_is_preserved():
    assert DogzillaController(sdk=FakeDOGZILLA(), max_speed=0).max_speed == 0
    assert DogzillaController(sdk=FakeDOGZILLA()).max_speed == 1.0
