"""Tests for RobotInterface, RobotSimulator, and Robot API endpoints."""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.robot.simulator import RobotSimulator


def test_robot_simulator_initial_state():
    """Verifies default simulator state upon initialization."""
    sim = RobotSimulator()
    status = sim.get_status()
    assert status["connected"] is True
    assert status["mode"] == "simulation"
    assert status["battery_level"] > 90.0
    assert status["speed"] == 0.0
    assert status["last_command"] == "stop"
    assert status["emergency_stopped"] is False
    assert status["position"]["x"] == 0.0
    assert status["position"]["y"] == 0.0
    assert status["position"]["heading"] == 0.0


def test_robot_simulator_movement():
    """Verifies forward, backward, turn, and stop calculations."""
    sim = RobotSimulator()

    # Move forward (heading 0 deg -> along positive X)
    res_fwd = sim.move("forward", speed=0.5)
    assert res_fwd["success"] is True
    assert sim.x == 0.5
    assert sim.y == 0.0
    assert sim.last_command == "forward"

    # Turn left (heading + 15 deg)
    res_left = sim.move("left", speed=0.2)
    assert res_left["success"] is True
    assert sim.heading == 15.0

    # Turn right (heading - 15 deg -> 0)
    res_right = sim.move("right", speed=0.2)
    assert res_right["success"] is True
    assert sim.heading == 0.0

    # Stop
    res_stop = sim.stop()
    assert res_stop["success"] is True
    assert sim.speed == 0.0
    assert sim.last_command == "stop"


def test_robot_emergency_stop_lifecycle():
    """Verifies that emergency stop halts the robot and locks subsequent movement until reset."""
    sim = RobotSimulator()

    # Engage emergency stop
    res_estop = sim.emergency_stop()
    assert res_estop["success"] is True
    assert sim.emergency_stopped is True
    assert sim.speed == 0.0

    # Attempting to move must be rejected
    res_blocked = sim.move("forward", speed=0.5)
    assert res_blocked["success"] is False
    assert "blocked" in res_blocked["message"].lower()

    # Reset emergency stop
    res_reset = sim.reset_emergency_stop()
    assert res_reset["success"] is True
    assert sim.emergency_stopped is False

    # Movement should succeed now
    res_resumed = sim.move("forward", speed=0.3)
    assert res_resumed["success"] is True


def test_robot_simulator_stop_actions_via_move():
    """'stop' and 'emergency_stop' sent through move() behave like the dedicated methods."""
    sim = RobotSimulator()
    sim.move("forward", speed=0.5)

    res_stop = sim.move("stop")
    assert res_stop["success"] is True
    assert sim.speed == 0.0

    res_estop = sim.move("emergency_stop")
    assert res_estop["success"] is True
    assert sim.emergency_stopped is True

    # Stop is still accepted while e-stopped and keeps the e-stop marker
    res_stop_again = sim.move("stop")
    assert res_stop_again["success"] is True
    assert sim.last_command == "emergency_stop"
    assert sim.emergency_stopped is True


def test_robot_simulator_rejects_bad_input():
    """Unknown actions, non-string actions, and invalid speeds are rejected without moving."""
    sim = RobotSimulator()

    assert sim.move("jump")["success"] is False
    assert sim.move(None)["success"] is False  # type: ignore[arg-type]
    assert sim.move("forward", speed=float("nan"))["success"] is False
    assert sim.move("forward", speed="fast")["success"] is False  # type: ignore[arg-type]

    assert (sim.x, sim.y, sim.heading) == (0.0, 0.0, 0.0)


def test_robot_simulator_clamps_speed_and_wraps_heading():
    """Speed is clamped to 0..1 and heading wraps around 360 degrees."""
    sim = RobotSimulator()

    res = sim.move("forward", speed=5.0)
    assert res["success"] is True
    assert sim.speed == 1.0
    assert sim.x == 1.0

    sim.move("right")
    assert sim.heading == 345.0


def test_robot_simulator_battery_depleted_blocks_movement():
    """With an empty battery the simulator refuses to move."""
    sim = RobotSimulator(battery_level=0.05)

    assert sim.move("forward", speed=0.5)["success"] is True  # drains to 0
    assert sim.battery_level == 0.0

    res = sim.move("forward", speed=0.5)
    assert res["success"] is False
    assert "battery" in res["message"].lower()
    assert sim.speed == 0.0


def test_robot_api_endpoints():
    """Tests FastAPI /api/robot/status and /api/robot/command endpoints."""
    with TestClient(app) as client:
        # 1. Fetch status
        resp_status = client.get("/api/robot/status")
        assert resp_status.status_code == 200
        data = resp_status.json()
        assert "connected" in data
        assert "mode" in data

        # 2. Send forward command
        resp_cmd = client.post("/api/robot/command", json={"action": "forward", "speed": 0.2})
        assert resp_cmd.status_code == 200
        assert resp_cmd.json()["success"] is True

        # 3. Send stop command
        resp_stop = client.post("/api/robot/command", json={"action": "stop", "speed": 0.0})
        assert resp_stop.status_code == 200
        assert resp_stop.json()["success"] is True

        # 4. Trigger emergency stop
        resp_estop = client.post("/api/robot/command", json={"action": "emergency_stop", "speed": 0.0})
        assert resp_estop.status_code == 200

        # 5. Reset emergency stop
        resp_clear = client.post("/api/robot/reset-emergency")
        assert resp_clear.status_code == 200
