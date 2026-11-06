"""Tests for the vendored DOGZILLALib protocol helpers, the AI orchestrator's
DOGZILLA telemetry rules, and the laptop's legacy posture endpoint.

DogzillaController and the Pi server are covered by the v1 suites
(``tests/test_robot_dogzilla.py``, ``tests/test_robot_server.py``,
``tests/test_robot_e2e.py``).
"""

import struct

import pytest
from fastapi.testclient import TestClient

from backend.ai.providers.mock_provider import MockProvider
from backend.main import app as main_app
from backend.robot.DOGZILLALib import Byte2Float, PARAM, conver2float, conver2u8
from backend.robot.factory import create_robot


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


def test_factory_accepts_legacy_mode_string():
    sim_robot = create_robot("simulation")
    sim_robot.move("forward", speed=0.4)
    assert sim_robot.get_status()["speed"] == 0.4
    sim_robot.stop()
    assert sim_robot.get_status()["speed"] == 0.0


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
