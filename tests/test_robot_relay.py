"""Laptop Robot API relay tests with a mocked Pi transport."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, List

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.robot import protocol
from backend.robot.config import RobotConfig
from backend.robot.interface import RobotMode
from backend.robot.relay import RobotApiRelay
from backend.robot.relay_router import create_robot_relay_router


def make_client(handler, *, stop_handler=None):
    events: List[Dict[str, Any]] = []
    config = RobotConfig(
        mode=RobotMode.REMOTE,
        remote_url="http://pi.test",
        api_key="laptop-pi-secret",
        remote_timeout_s=0.4,
    )
    relay = RobotApiRelay(
        config,
        transport=httpx.MockTransport(handler),
        stop_transport=httpx.MockTransport(stop_handler or handler),
    )
    app = FastAPI()
    app.include_router(
        create_robot_relay_router(lambda: relay, lambda **kw: events.append(kw))
    )
    return TestClient(app), relay, events


def test_status_and_body_are_passed_through_unchanged():
    pi_body = b'{"status":"accepted","command":"motion","message":"ok"}'

    def pi(request: httpx.Request) -> httpx.Response:
        assert request.url.path == protocol.PATH_MOTION
        assert request.content == b'{"forward":0.5,"strafe":0,"turn":-0.2}'
        return httpx.Response(
            202,
            content=pi_body,
            headers={"content-type": "application/json"},
        )

    client, _, events = make_client(pi)
    response = client.post(
        protocol.PATH_MOTION,
        content=b'{"forward":0.5,"strafe":0,"turn":-0.2}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 202
    assert response.content == pi_body
    assert response.headers["content-type"] == "application/json"
    assert events[-1]["event_type"] == "robot_command"
    assert "motion" in events[-1]["description"]


def test_laptop_key_is_injected_and_session_sequence_headers_are_unchanged():
    seen: Dict[str, str] = {}

    def pi(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"status": "accepted", "command": "motion"})

    client, _, _ = make_client(pi)
    response = client.post(
        protocol.PATH_MOTION,
        json={"forward": 0, "strafe": 0, "turn": 0},
        headers={
            protocol.AUTH_HEADER: "untrusted-headset-key",
            protocol.SESSION_HEADER: "session-123",
            protocol.SEQ_HEADER: "0042",
            protocol.SENT_AT_HEADER: "1731000000123",
        },
    )

    assert response.status_code == 200
    assert seen[protocol.AUTH_HEADER.lower()] == "laptop-pi-secret"
    assert seen[protocol.SESSION_HEADER.lower()] == "session-123"
    assert seen[protocol.SEQ_HEADER.lower()] == "0042"
    assert seen[protocol.SENT_AT_HEADER.lower()] == "1731000000123"


def test_all_quest_robot_paths_forward_to_matching_pi_paths():
    seen: List[tuple[str, str]] = []

    def pi(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    client, _, _ = make_client(pi)
    calls = [
        ("POST", protocol.PATH_SESSION),
        ("DELETE", protocol.PATH_SESSION),
        ("POST", protocol.PATH_MOTION),
        ("POST", protocol.PATH_STOP),
        ("POST", protocol.PATH_MOVEMENT_STYLE),
        ("GET", protocol.PATH_ACTIONS),
        ("POST", protocol.PATH_ACTION.format(action_id=10)),
        ("GET", protocol.PATH_TELEMETRY),
        ("GET", protocol.PATH_STATUS),
    ]
    for method, path in calls:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 200

    assert seen == calls


def test_pi_down_returns_503_instead_of_leaking_transport_error():
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Pi is offline")

    client, _, events = make_client(down)
    response = client.post(
        protocol.PATH_MOTION,
        json={"forward": 1, "strafe": 0, "turn": 0},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Pi Robot API unavailable."}
    assert events[-1]["event_type"] == "robot_command"


def test_stop_uses_priority_lane_while_motion_is_in_flight():
    motion_started = threading.Event()
    release_motion = threading.Event()
    motion_result: Dict[str, Any] = {}

    async def normal_pi(request: httpx.Request) -> httpx.Response:
        assert request.url.path == protocol.PATH_MOTION
        motion_started.set()
        await asyncio.to_thread(release_motion.wait, 2.0)
        return httpx.Response(200, json={"status": "accepted", "command": "motion"})

    def priority_pi(request: httpx.Request) -> httpx.Response:
        assert request.url.path == protocol.PATH_STOP
        return httpx.Response(200, json={"status": "stop_dispatched", "command": "stop"})

    client, _, events = make_client(normal_pi, stop_handler=priority_pi)

    def send_motion() -> None:
        motion_result["response"] = client.post(
            protocol.PATH_MOTION,
            json={"forward": 1, "strafe": 0, "turn": 0},
        )

    thread = threading.Thread(target=send_motion)
    thread.start()
    assert motion_started.wait(1.0), "motion did not reach the fake Pi"
    stop_response = client.post(protocol.PATH_STOP)

    assert stop_response.status_code == 200
    assert stop_response.json()["command"] == "stop"
    release_motion.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert motion_result["response"].status_code == 200
    assert any("stop" in event["description"] for event in events)


def test_action_execution_is_audited_without_broadcast_dependency():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(423, content=json.dumps({"detail": "stopped"}))

    client, _, events = make_client(pi)
    response = client.post(protocol.PATH_ACTION.format(action_id=16))

    assert response.status_code == 423
    assert events[-1]["event_type"] == "robot_command"
    assert "action:16" in events[-1]["description"]
