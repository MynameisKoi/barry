"""Hardware-free contract tests for the DOGZILLA adapter."""
import threading
import time

from backend.robot.config import RobotConfig
from backend.robot.dogzilla import DogzillaController
from backend.robot.interface import (
    CommandStatus, Direction, Gait, HeightPreset, Intensity, MotionCommand,
    MovementStyle, Posture,
)


class FakeDOGZILLA:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.overlap = False
        self.fail_read = set()

    def _record(self, name, *args):
        self.active += 1
        if self.active > 1:
            self.overlap = True
        time.sleep(0.001)
        self.calls.append((name, *args))
        self.active -= 1

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
