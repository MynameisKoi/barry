"""Router-level tests for backend/robot/router.py, independent of backend.main."""

import asyncio
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.robot.router import create_robot_router
from backend.robot.simulator import RobotSimulator


def make_client(robot=None):
    events: List[Dict[str, Any]] = []
    messages: List[Dict[str, Any]] = []
    holder = {"robot": robot if robot is not None else RobotSimulator()}

    def get_robot():
        if holder["robot"] is None:
            raise HTTPException(status_code=503, detail="Robot controller is not initialized.")
        return holder["robot"]

    async def broadcast(msg):
        messages.append(msg)

    app = FastAPI()
    app.include_router(create_robot_router(get_robot, lambda **kw: events.append(kw), broadcast))
    return TestClient(app), holder, events, messages


def test_status_route():
    client, *_ = make_client()
    resp = client.get("/api/robot/status")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "simulation"


def test_status_allows_missing_battery():
    class NoBattery(RobotSimulator):
        def get_status(self):
            out = super().get_status()
            out["battery_level"] = None
            return out

    client, *_ = make_client(NoBattery())
    resp = client.get("/api/robot/status")
    assert resp.status_code == 200
    assert resp.json()["battery_level"] is None


def test_command_logs_and_broadcasts():
    client, _, events, messages = make_client()
    resp = client.post("/api/robot/command", json={"action": "forward", "speed": 0.3})
    assert resp.status_code == 200
    assert events[-1]["event_type"] == "robot_command"
    assert messages[-1]["type"] == "robot_command_feedback"
    assert messages[-1]["action"] == "forward"


def test_command_failure_is_400():
    client, *_ = make_client()
    client.post("/api/robot/command", json={"action": "emergency_stop"})
    resp = client.post("/api/robot/command", json={"action": "forward", "speed": 0.3})
    assert resp.status_code == 400


def test_estop_reset_stop_and_posture_routes():
    client, _, events, messages = make_client()
    client.post("/api/robot/command", json={"action": "emergency_stop"})
    assert events[-1]["event_type"] == "emergency_stop"

    assert client.post("/api/robot/reset-emergency").status_code == 200
    assert events[-1]["event_type"] == "emergency_stop_reset"
    assert messages[-1]["type"] == "robot_status"

    assert client.post("/api/robot/stop").status_code == 200

    ok = client.post("/api/robot/posture", json={"preset": "crouch"})
    assert ok.status_code == 200
    assert events[-1]["event_type"] == "robot_posture"

    bad = client.post("/api/robot/posture", json={"preset": "typo"})
    assert bad.status_code == 400


def test_routes_503_without_robot():
    client, holder, *_ = make_client()
    holder["robot"] = None
    assert client.get("/api/robot/status").status_code == 503
    assert client.post("/api/robot/stop").status_code == 503
