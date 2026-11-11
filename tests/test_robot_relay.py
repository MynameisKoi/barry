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
from backend.robot.relay_router import QuestRelayAuth, create_robot_relay_router


QUEST_KEY = "quest-secret"


def make_client(handler, *, stop_handler=None, quest_key=QUEST_KEY):
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
        create_robot_relay_router(
            lambda: relay,
            lambda **kw: events.append(kw),
            QuestRelayAuth(quest_key),
        )
    )
    return TestClient(app, headers={protocol.AUTH_HEADER: QUEST_KEY}), relay, events


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
    assert len(events) == 1
    assert "motion started" in events[0]["description"]


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
            protocol.AUTH_HEADER: QUEST_KEY,
            protocol.SESSION_HEADER: "session-123",
            protocol.SEQ_HEADER: "0042",
            protocol.SENT_AT_HEADER: "1731000000123",
        },
    )

    assert response.status_code == 200
    assert seen[protocol.AUTH_HEADER.lower()] == "laptop-pi-secret"
    assert QUEST_KEY not in seen.values()
    assert "untrusted-headset-key" not in seen.values()
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
        ("POST", protocol.PATH_MOTION_DISCRETE),
        ("POST", protocol.PATH_STOP),
        ("POST", protocol.PATH_EMERGENCY_STOP),
        ("POST", protocol.PATH_EMERGENCY_RESET),
        ("POST", protocol.PATH_MOVEMENT_STYLE),
        ("POST", protocol.PATH_POSTURE),
        ("POST", protocol.PATH_RESET),
        ("GET", protocol.PATH_ACTIONS),
        ("POST", protocol.PATH_ACTION.format(action_id=10)),
        ("GET", protocol.PATH_TELEMETRY),
        ("GET", protocol.PATH_STATUS),
    ]
    for method, path in calls:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 200

    assert seen == calls


def test_relay_requires_quest_key_on_every_route():
    calls: List[tuple[str, str]] = []

    def pi(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    client, _, _ = make_client(pi)
    paths = [
        ("POST", protocol.PATH_SESSION),
        ("DELETE", protocol.PATH_SESSION),
        ("POST", protocol.PATH_MOTION),
        ("POST", protocol.PATH_MOTION_DISCRETE),
        ("POST", protocol.PATH_STOP),
        ("POST", protocol.PATH_EMERGENCY_STOP),
        ("POST", protocol.PATH_EMERGENCY_RESET),
        ("POST", protocol.PATH_MOVEMENT_STYLE),
        ("POST", protocol.PATH_POSTURE),
        ("POST", protocol.PATH_RESET),
        ("GET", protocol.PATH_ACTIONS),
        ("POST", protocol.PATH_ACTION.format(action_id=10)),
        ("GET", protocol.PATH_TELEMETRY),
        ("GET", protocol.PATH_STATUS),
    ]
    for method, path in paths:
        for headers in ({protocol.AUTH_HEADER: ""}, {protocol.AUTH_HEADER: "wrong"}):
            response = client.request(
                method, path, json={} if method == "POST" else None, headers=headers
            )
            assert response.status_code == 401
    assert calls == []


def test_unconfigured_quest_key_fails_closed():
    def pi(_: httpx.Request) -> httpx.Response:
        raise AssertionError("unauthenticated request reached the Pi")

    client, _, _ = make_client(pi, quest_key="   ")
    response = client.get(protocol.PATH_STATUS)

    assert response.status_code == 503
    assert response.json() == {
        "detail": "robot relay authentication is not configured"
    }


def test_motion_audit_only_logs_transitions_and_failures():
    events: List[Dict[str, Any]] = []

    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "accepted"})

    client, _, events = make_client(pi)
    for _ in range(50):
        response = client.post(
            protocol.PATH_MOTION, json={"forward": 0.5, "strafe": 0, "turn": 0}
        )
        assert response.status_code == 200
    assert len(events) == 1
    assert "motion started" in events[0]["description"]

    response = client.post(
        protocol.PATH_MOTION, json={"forward": 0, "strafe": 0, "turn": 0}
    )
    assert response.status_code == 200
    assert len(events) == 2
    assert "motion stopped" in events[1]["description"]


def test_stop_resets_motion_audit_state_for_next_drive():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "accepted"})

    client, _, events = make_client(pi)
    motion = {"forward": 0.5, "strafe": 0, "turn": 0}
    assert client.post(protocol.PATH_MOTION, json=motion).status_code == 200
    assert client.post(protocol.PATH_STOP).status_code == 200
    assert client.post(protocol.PATH_MOTION, json=motion).status_code == 200

    assert sum("motion started" in event["description"] for event in events) == 2
    assert sum("Relayed stop" in event["description"] for event in events) == 1


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
    assert "HTTP 503" in events[-1]["description"]


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
    assert sum("stop" in event["description"] for event in events) == 1


def test_action_execution_is_audited_without_broadcast_dependency():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(423, content=json.dumps({"detail": "stopped"}))

    client, _, events = make_client(pi)
    response = client.post(protocol.PATH_ACTION.format(action_id=16))

    assert response.status_code == 423
    assert events[-1]["event_type"] == "robot_command"
    assert "action:16" in events[-1]["description"]


def test_stop_and_action_are_audited_for_every_request():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client, _, events = make_client(pi)
    for _ in range(3):
        assert client.post(protocol.PATH_STOP).status_code == 200
        assert client.post(protocol.PATH_ACTION.format(action_id=16)).status_code == 200

    assert len(events) == 6
    assert sum("Relayed stop" in event["description"] for event in events) == 3
    assert sum("action:16" in event["description"] for event in events) == 3


DISCRETE = {"direction": "forward", "intensity": "low"}


def test_posture_body_and_pi_key_are_relayed():
    seen: Dict[str, Any] = {}

    def pi(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers[protocol.AUTH_HEADER]
        return httpx.Response(200, json={"status": "accepted", "command": "posture"})

    client, _, events = make_client(pi)
    body = {"height": "medium", "roll": 1.5, "pitch": 0, "yaw": 0}
    response = client.post(protocol.PATH_POSTURE, json=body)

    assert response.status_code == 200 and response.json()["command"] == "posture"
    assert seen == {"path": protocol.PATH_POSTURE, "body": body, "key": "laptop-pi-secret"}
    assert events == []  # posture is not a motion/safety command; no audit noise


def test_emergency_stop_uses_priority_lane_while_motion_is_in_flight():
    motion_started = threading.Event()
    release_motion = threading.Event()
    motion_result: Dict[str, Any] = {}

    async def normal_pi(request: httpx.Request) -> httpx.Response:
        # The normal lane must only ever see the motion request; an e-stop here would fail.
        assert request.url.path == protocol.PATH_MOTION
        motion_started.set()
        await asyncio.to_thread(release_motion.wait, 2.0)
        return httpx.Response(200, json={"status": "accepted", "command": "motion"})

    def priority_pi(request: httpx.Request) -> httpx.Response:
        assert request.url.path == protocol.PATH_EMERGENCY_STOP
        return httpx.Response(200, json={"status": "stop_dispatched", "command": "emergency_stop"})

    client, _, events = make_client(normal_pi, stop_handler=priority_pi)

    def send_motion() -> None:
        motion_result["response"] = client.post(
            protocol.PATH_MOTION, json={"forward": 1, "strafe": 0, "turn": 0}
        )

    thread = threading.Thread(target=send_motion)
    thread.start()
    assert motion_started.wait(1.0), "motion did not reach the fake Pi"
    response = client.post(protocol.PATH_EMERGENCY_STOP)

    assert response.status_code == 200
    assert response.json()["command"] == "emergency_stop"
    release_motion.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert motion_result["response"].status_code == 200
    assert sum("emergency_stop" in event["description"] for event in events) == 1


def test_emergency_stop_reset_and_reset_are_not_sent_on_the_priority_lane():
    lanes: List[tuple[str, str]] = []

    def normal(request: httpx.Request) -> httpx.Response:
        lanes.append(("normal", request.url.path))
        return httpx.Response(200, json={"ok": True})

    def priority(request: httpx.Request) -> httpx.Response:
        lanes.append(("priority", request.url.path))
        return httpx.Response(200, json={"ok": True})

    client, _, _ = make_client(normal, stop_handler=priority)
    for path in (protocol.PATH_STOP, protocol.PATH_EMERGENCY_STOP, protocol.PATH_EMERGENCY_RESET,
                 protocol.PATH_RESET, protocol.PATH_POSTURE, protocol.PATH_MOTION_DISCRETE):
        assert client.post(path, json=DISCRETE if path == protocol.PATH_MOTION_DISCRETE else {}).status_code == 200

    assert lanes == [
        ("priority", protocol.PATH_STOP),
        ("priority", protocol.PATH_EMERGENCY_STOP),
        ("normal", protocol.PATH_EMERGENCY_RESET),
        ("normal", protocol.PATH_RESET),
        ("normal", protocol.PATH_POSTURE),
        ("normal", protocol.PATH_MOTION_DISCRETE),
    ]


def test_emergency_stop_reset_and_reset_are_audited_for_every_request():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client, _, events = make_client(pi)
    for _ in range(3):
        assert client.post(protocol.PATH_EMERGENCY_STOP).status_code == 200
        assert client.post(protocol.PATH_EMERGENCY_RESET).status_code == 200
        assert client.post(protocol.PATH_RESET).status_code == 200

    assert len(events) == 9
    descriptions = [event["description"] for event in events]
    assert sum("emergency_stop to" in d for d in descriptions) == 3
    assert sum("emergency_stop_reset" in d for d in descriptions) == 3
    assert sum("Relayed reset" in d for d in descriptions) == 3
    assert all(event["event_type"] == "robot_command" for event in events)


def test_safety_commands_are_audited_even_when_the_pi_fails_or_is_down():
    def pi(request: httpx.Request) -> httpx.Response:
        if request.url.path == protocol.PATH_RESET:
            return httpx.Response(423, json={"detail": "latched"})
        raise httpx.ConnectError("pi down")

    client, _, events = make_client(pi)
    assert client.post(protocol.PATH_EMERGENCY_STOP).status_code == 503
    assert client.post(protocol.PATH_EMERGENCY_RESET).status_code == 503
    assert client.post(protocol.PATH_RESET).status_code == 423
    assert [("HTTP 503" in e["description"]) for e in events[:2]] == [True, True]
    assert "HTTP 423" in events[2]["description"]


def test_discrete_motion_audit_only_logs_transitions_and_failures():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "accepted"})

    client, _, events = make_client(pi)
    for _ in range(50):
        assert client.post(protocol.PATH_MOTION_DISCRETE, json=DISCRETE).status_code == 200
    assert len(events) == 1 and "motion started" in events[0]["description"]

    assert client.post(protocol.PATH_STOP).status_code == 200      # stop ends the drive...
    assert client.post(protocol.PATH_MOTION_DISCRETE, json=DISCRETE).status_code == 200
    assert sum("motion started" in e["description"] for e in events) == 2   # ...so a new one is logged


def test_discrete_and_analog_motion_share_the_active_state():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "accepted"})

    client, _, events = make_client(pi)
    assert client.post(protocol.PATH_MOTION, json={"forward": 0.5, "strafe": 0, "turn": 0}).status_code == 200
    assert client.post(protocol.PATH_MOTION_DISCRETE, json=DISCRETE).status_code == 200  # already moving
    assert len(events) == 1
    assert client.post(protocol.PATH_MOTION, json={"forward": 0, "strafe": 0, "turn": 0}).status_code == 200
    assert "motion stopped" in events[-1]["description"]


def test_discrete_motion_failures_are_audited():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Command invalidated by stop"})

    client, _, events = make_client(pi)
    for _ in range(2):
        assert client.post(protocol.PATH_MOTION_DISCRETE, json=DISCRETE).status_code == 409
    assert len(events) == 2 and all("HTTP 409" in e["description"] for e in events)


def test_emergency_stop_and_reset_end_the_motion_audit_state():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "accepted"})

    for ender in (protocol.PATH_EMERGENCY_STOP, protocol.PATH_RESET):
        client, _, events = make_client(pi)
        assert client.post(protocol.PATH_MOTION_DISCRETE, json=DISCRETE).status_code == 200
        assert client.post(ender).status_code == 200
        assert client.post(protocol.PATH_MOTION_DISCRETE, json=DISCRETE).status_code == 200
        assert sum("motion started" in e["description"] for e in events) == 2, ender
