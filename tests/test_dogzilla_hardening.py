"""Tests for DOGZILLALib protocol helpers, DogzillaController hardware scaling,
Raspberry Pi Robot Server + Watchdog, and AI Orchestrator DOGZILLA telemetry rules.
"""

import struct
import time
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from backend.ai.providers.mock_provider import MockProvider
from backend.main import app as main_app
from backend.robot.DOGZILLALib import Byte2Float, PARAM, conver2float, conver2u8
from backend.robot.dogzilla import DogzillaController
from backend.robot.factory import create_robot
from backend.robot.remote import RemoteRobotClient
from backend.robot.server import WatchdogController, app as pi_app, pi_config


class FakeDogzillaDriver:
    """In-memory mock of DOGZILLALib.DOGZILLA for testing serial command translation."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.battery = 84
        self.roll = 1.5
        self.pitch = -2.0
        self.yaw = 45.0

    def forward(self, step: int) -> None:
        self.calls.append(("forward", (step,)))

    def back(self, step: int) -> None:
        self.calls.append(("back", (step,)))

    def left(self, step: int) -> None:
        self.calls.append(("left", (step,)))

    def right(self, step: int) -> None:
        self.calls.append(("right", (step,)))

    def turnleft(self, step: int) -> None:
        self.calls.append(("turnleft", (step,)))

    def turnright(self, step: int) -> None:
        self.calls.append(("turnright", (step,)))

    def stop(self) -> None:
        self.calls.append(("stop", ()))

    def reset(self) -> None:
        self.calls.append(("reset", ()))

    def unload_allmotor(self) -> None:
        self.calls.append(("unload_allmotor", ()))

    def load_allmotor(self) -> None:
        self.calls.append(("load_allmotor", ()))

    def translation(self, direction: str, data: float) -> None:
        self.calls.append(("translation", (direction, data)))

    def attitude(self, direction: str, data: float) -> None:
        self.calls.append(("attitude", (direction, data)))

    def gait_type(self, mode: str) -> None:
        self.calls.append(("gait_type", (mode,)))

    def pace(self, mode: str) -> None:
        self.calls.append(("pace", (mode,)))

    def imu(self, mode: int) -> None:
        self.calls.append(("imu", (mode,)))

    def read_battery(self) -> int:
        return self.battery

    def read_roll(self) -> float:
        return self.roll

    def read_pitch(self) -> float:
        return self.pitch

    def read_yaw(self) -> float:
        return self.yaw

    def read_version(self) -> str:
        return "2.0.7"


def test_dogzillalib_byte_conversions():
    # Neutral 0 maps to 128 for symmetric limits
    assert conver2u8(0, PARAM["VX_LIMIT"]) == 128
    assert conver2u8(25, PARAM["VX_LIMIT"]) == 255
    assert conver2u8(-25, PARAM["VX_LIMIT"]) == 0

    # Z height [75, 115] maps 75 -> 0, 115 -> 255
    assert conver2u8(75, [75, 115]) == 0
    assert conver2u8(115, [75, 115]) == 255
    assert round(conver2float(255, [75, 115]), 1) == 115.0

    # Little-endian 4-byte float unpacking used by read_roll/pitch/yaw
    packed_le = struct.pack("<f", 12.5)
    assert round(Byte2Float(packed_le), 2) == 12.5


def test_dogzilla_controller_scaling_posture_and_estop():
    driver = FakeDogzillaDriver()
    ctrl = DogzillaController(driver=driver, max_speed=1.0)

    # 1. Telemetry read from driver on init
    status = ctrl.get_status()
    assert status["connected"] is True
    assert status["battery_level"] == 84.0
    assert status["imu"]["roll"] == 1.5
    assert status["imu"]["pitch"] == -2.0
    assert status["position"]["heading"] == 45.0

    # 2. Forward movement scales normalized speed 0.5 -> step 10 mm (not 0.5!)
    res_fwd = ctrl.move("forward", speed=0.5)
    assert res_fwd["success"] is True
    assert ("forward", (10,)) in driver.calls

    # 3. Turn left scales normalized speed 0.5 -> 50 deg/s (respecting [30, 70] deadband)
    res_turn = ctrl.move("left", speed=0.5)
    assert res_turn["success"] is True
    assert ("turnleft", (50,)) in driver.calls

    # 4. Strafe right scales normalized speed 0.5 -> 9 mm
    res_strafe = ctrl.move("strafe_right", speed=0.5)
    assert res_strafe["success"] is True
    assert ("right", (9,)) in driver.calls

    # 5. Obstacle crossing preset configures high_walk + slow + z=95
    res_posture = ctrl.set_posture(preset="obstacle_crossing", imu_enabled=True, pitch=10.0)
    assert res_posture["success"] is True
    assert ("gait_type", ("high_walk",)) in driver.calls
    assert ("pace", ("slow",)) in driver.calls
    assert ("translation", ("z", 95)) in driver.calls
    assert ("imu", (1,)) in driver.calls
    assert ("attitude", ("p", 10.0)) in driver.calls

    # 6. Emergency stop via move("emergency_stop") halts, resets, and unloads all 12 motors
    res_estop = ctrl.move("emergency_stop")
    assert res_estop["success"] is True
    assert ctrl.emergency_stopped is True
    assert ("unload_allmotor", ()) in driver.calls

    # Subsequent moves blocked while E-stop active
    blocked = ctrl.move("forward", speed=0.4)
    assert blocked["success"] is False
    assert blocked["error_code"] == "ESTOP_ACTIVE"

    # Reset E-stop reloads motors and restores standing posture
    res_reset = ctrl.reset_emergency_stop()
    assert res_reset["success"] is True
    assert ctrl.emergency_stopped is False
    assert ("load_allmotor", ()) in driver.calls


def test_pi_robot_server_auth_speed_cap_and_watchdog():
    sim_robot = create_robot("simulation")
    wd = WatchdogController(sim_robot, timeout_ms=100)

    sim_robot.move("forward", speed=0.4)
    wd.feed("forward")
    assert wd.is_moving is True
    assert sim_robot.get_status()["speed"] == 0.4

    time.sleep(0.12)
    expired = wd.check_and_stop_if_expired()
    assert expired is True
    assert wd.is_moving is False
    assert sim_robot.get_status()["speed"] == 0.0

    # Verify Pi Server API key enforcement and emergency speed cap
    orig_primary = pi_config.ROBOT_API_KEY
    orig_emerg = pi_config.ROBOT_EMERGENCY_KEY
    try:
        pi_config.ROBOT_API_KEY = "laptop-secret-key"
        pi_config.ROBOT_EMERGENCY_KEY = "quest-emergency-key"

        with TestClient(pi_app) as client:
            # Unauthenticated /healthz works
            assert client.get("/healthz").status_code == 200

            # Missing X-Robot-Key rejected with 401
            assert client.get("/api/robot/status").status_code == 401

            # Emergency key caps speed to ROBOT_EMERGENCY_MAX_SPEED (0.35)
            client.post(
                "/api/robot/reset-emergency",
                headers={"X-Robot-Key": "laptop-secret-key"},
            )
            cmd_resp = client.post(
                "/api/robot/command",
                headers={"X-Robot-Key": "quest-emergency-key"},
                json={"action": "forward", "speed": 0.9},
            )
            assert cmd_resp.status_code == 200
            assert cmd_resp.json()["telemetry"]["speed"] <= pi_config.ROBOT_EMERGENCY_MAX_SPEED
    finally:
        pi_config.ROBOT_API_KEY = orig_primary
        pi_config.ROBOT_EMERGENCY_KEY = orig_emerg


@pytest.mark.asyncio
async def test_ai_mock_provider_dogzilla_imu_and_battery_hazards():
    provider = MockProvider()

    # 1. Excessive IMU roll (>= 15 deg) triggers high-risk tip-over warning + posture recommendation
    imu_res = await provider.analyze_mission(
        {
            "event_type": "telemetry",
            "data": {
                "zone": "Rubble Slope C",
                "roll": 17.5,
                "pitch": 4.0,
            },
        }
    )
    assert imu_res["risk_level"] == "high"
    assert imu_res["requires_human_review"] is True
    assert "imu_tilt_instability" in imu_res["detected_objects"]
    assert "z=75mm" in imu_res["recommended_action"]

    # 2. Critical low battery (<= 15%) triggers high-risk alert
    bat_res = await provider.analyze_mission(
        {
            "event_type": "telemetry",
            "data": {
                "zone": "Tunnel West",
                "battery": 11.0,
            },
        }
    )
    assert bat_res["risk_level"] == "high"
    assert "critical_low_battery" in bat_res["detected_objects"]

    # 3. Obstacle detection recommends DOGZILLA obstacle_crossing preset
    obs_res = await provider.analyze_mission(
        {
            "event_type": "object_detection",
            "data": {
                "object": "obstacle",
                "zone": "Pipe Corridor",
                "confidence": 0.89,
            },
        }
    )
    assert obs_res["risk_level"] == "medium"
    assert "obstacle_crossing" in obs_res["recommended_action"]


def test_main_api_robot_posture_endpoint():
    with TestClient(main_app) as client:
        client.post("/api/robot/reset-emergency")
        resp = client.post(
            "/api/robot/posture",
            json={"preset": "obstacle_crossing", "imu_enabled": True, "pitch": -8.0},
        )
        assert resp.status_code == 200
        telem = resp.json()["telemetry"]
        assert telem["posture"]["gait_type"] == "high_walk"
        assert telem["posture"]["pace"] == "slow"
        assert telem["posture"]["height_z"] == 95.0
        assert telem["imu"]["imu_stabilization"] is True
        assert telem["imu"]["pitch"] == -8.0


def test_remote_robot_client_offline_fallback():
    """Verify RemoteRobotClient returns offline telemetry safely when server unreachable."""
    client = RemoteRobotClient(base_url="http://127.0.0.1:59999", timeout=0.2)
    status = client.get_status()
    assert status["connected"] is False
    assert status["mode"] == "remote"

    move_res = client.move("forward", 0.3)
    assert move_res["success"] is False
    assert move_res["error_code"] == "OFFLINE"
    assert move_res["telemetry"]["connected"] is False

