"""Laptop Robot API relay tests with a mocked Pi transport."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, List

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.robot import protocol
from backend.robot.config import RobotConfig
from backend.robot.interface import RobotMode
from backend.robot.remote import RemoteRobotClient
from backend.robot.relay_router import QuestRelayAuth, create_robot_relay_router
from backend.gateway.gateway import CommandGateway


QUEST_KEY = "quest-secret"


class GatewayTestClient(TestClient):
    """Test client that speaks Quest's session and monotonically increasing sequence contract."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_session = None
        self.command_seq = 0
        self._sequence_lock = threading.Lock()

    def request(self, method, url, **kwargs):
        path = str(url).split("?")[0]
        headers = dict(kwargs.pop("headers", {}) or {})
        if path.endswith(protocol.PATH_SESSION) and method.upper() == "POST":
            response = super().request(method, url, headers=headers, **kwargs)
            if response.status_code in (200, 201):
                self.control_session = response.json()["session_id"]
                self.command_seq = 0
            return response
        if method.upper() in {"POST", "DELETE"} and path.startswith(protocol.API_PREFIX):
            if path not in {protocol.PATH_STOP, protocol.PATH_EMERGENCY_STOP}:
                if self.control_session is None:
                    self.request("POST", protocol.PATH_SESSION)
                if protocol.SESSION_HEADER not in headers:
                    with self._sequence_lock:
                        self.command_seq += 1
                        headers[protocol.SESSION_HEADER] = self.control_session
                        headers[protocol.SEQ_HEADER] = str(self.command_seq)
        response = super().request(method, url, headers=headers, **kwargs)
        if method.upper() == "DELETE" and path.endswith(protocol.PATH_SESSION) and response.status_code == 200:
            self.control_session = None
        return response


def make_client(handler, *, stop_handler=None, quest_key=QUEST_KEY):
    events: List[Dict[str, Any]] = []
    config = RobotConfig(
        mode=RobotMode.REMOTE,
        remote_url="http://pi.test",
        api_key="laptop-pi-secret",
        remote_timeout_s=2.0,
    )
    def pi(request):
        if request.url.path == protocol.PATH_SESSION and request.method == "POST":
            return httpx.Response(201, json={"session_id": "gateway-pi-session"})
        responder = stop_handler if stop_handler and request.url.path in {
            protocol.PATH_STOP, protocol.PATH_EMERGENCY_STOP,
        } else handler
        try:
            response = responder(request)
        except AssertionError:
            raise
        except Exception as exc:
            if isinstance(exc, httpx.RequestError):
                raise
            raise
        if 200 <= response.status_code < 300 and request.method == "POST":
            try:
                body = response.json()
            except ValueError:
                body = {}
            if "status" not in body and request.url.path not in {protocol.PATH_EMERGENCY_STOP}:
                body = {"status": "stop_dispatched" if request.url.path == protocol.PATH_STOP else "accepted",
                        "command": request.url.path.rsplit("/", 1)[-1], **body}
                return httpx.Response(response.status_code, json=body, headers=dict(response.headers))
        return response

    robot = RemoteRobotClient(config, transport=httpx.MockTransport(pi))
    robot._motion_timeout_s = min(config.remote_timeout_s, 2.0)
    gateway = CommandGateway(robot, mission_check=lambda: True, max_bounded_s=0.2)
    app = FastAPI()
    app.include_router(
        create_robot_relay_router(
            lambda: gateway,
            lambda **kw: events.append(kw),
            QuestRelayAuth(quest_key),
        )
    )
    return GatewayTestClient(app, headers={protocol.AUTH_HEADER: QUEST_KEY}), robot, events


def test_status_and_body_are_passed_through_unchanged():
    pi_body = b'{"status":"accepted","command":"motion","message":"ok"}'

    def pi(request: httpx.Request) -> httpx.Response:
        assert request.url.path == protocol.PATH_MOTION
        assert json.loads(request.content) == {"forward": 0.5, "strafe": 0.0, "turn": -0.2}
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

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["command"] == "motion"
    assert response.json()["message"] == "ok"
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
            protocol.SOURCE_HEADER: "voice",
            protocol.COMMAND_ID_HEADER: "cmd-42",
        },
    )

    assert response.status_code == 200
    assert seen[protocol.AUTH_HEADER.lower()] == "laptop-pi-secret"
    assert QUEST_KEY not in seen.values()
    assert "untrusted-headset-key" not in seen.values()
    assert seen[protocol.SESSION_HEADER.lower()] == "gateway-pi-session"
    assert int(seen[protocol.SEQ_HEADER.lower()]) > 0
    assert protocol.SENT_AT_HEADER.lower() in seen
    assert seen[protocol.SOURCE_HEADER.lower()] == "hand"
    assert seen[protocol.COMMAND_ID_HEADER.lower()] != "cmd-42"


def test_discrete_motion_uses_short_motion_timeout():
    seen: List[float] = []

    def pi(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"ok": True})

    client, relay, _ = make_client(pi)
    assert client.post(protocol.PATH_MOTION_DISCRETE, json={"direction": "forward"}).status_code == 200
    # Discrete moves use the same motion timeout as /motion (whatever value config gives it).
    assert seen[0] == relay._motion_timeout_s


def test_relay_logs_command_metadata_result_and_latency(caplog):
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client, _, events = make_client(pi)
    client.post(protocol.PATH_MOTION,
                json={"forward": 0.5, "strafe": 0, "turn": 0},
                headers={protocol.SOURCE_HEADER: "llm", protocol.COMMAND_ID_HEADER: "llm-7"})
    assert events[-1]["result_code"] == 200
    assert events[-1]["source"] == "llm"
    assert events[-1]["command_id"] == "llm-7"
    assert events[-1]["latency_ms"] >= 0


def test_audit_metadata_is_sanitized_before_storage():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client, _, events = make_client(pi)
    client.post(protocol.PATH_STOP, headers={
        protocol.SOURCE_HEADER: "invalid", protocol.COMMAND_ID_HEADER: "  id\r\n" + "x" * 80,
    })
    assert events[0]["source"] == "unknown"
    assert events[0]["command_id"] == "id" + "x" * 62


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
        payload = {"direction": "forward"} if path == protocol.PATH_MOTION_DISCRETE else (
            {"intensity": "medium", "gait": "trot"} if path == protocol.PATH_MOVEMENT_STYLE else
            {"height": "medium", "roll": 0, "pitch": 0, "yaw": 0} if path == protocol.PATH_POSTURE else {})
        response = client.request(method, path, json=payload if method == "POST" else None)
        assert response.status_code in (200, 201)

    assert ("POST", protocol.PATH_MOTION) in seen
    assert ("POST", protocol.PATH_MOTION_DISCRETE) in seen
    assert ("POST", protocol.PATH_EMERGENCY_RESET) in seen
    assert ("POST", protocol.PATH_ACTION.format(action_id=10)) in seen
    assert ("GET", protocol.PATH_TELEMETRY) in seen
    assert ("DELETE", protocol.PATH_SESSION) not in seen  # Quest session is gateway-level.


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
        # stop and emergency-stop are deliberately unkeyed: see test_stop_and_estop_work_without_quest_key.
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


@pytest.mark.parametrize("path", [protocol.PATH_STOP, protocol.PATH_EMERGENCY_STOP])
@pytest.mark.parametrize(
    "quest_key, headers",
    [
        (QUEST_KEY, {protocol.AUTH_HEADER: ""}),       # headset sent no key
        (QUEST_KEY, {protocol.AUTH_HEADER: "wrong"}),  # headset sent a wrong key
        ("   ", {protocol.AUTH_HEADER: ""}),           # laptop has no QUEST_API_KEY at all
    ],
)
def test_stop_and_estop_work_without_quest_key(path, quest_key, headers):
    seen: List[Dict[str, str]] = []

    def pi(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"ok": True})

    client, _, _ = make_client(pi, quest_key=quest_key)
    response = client.post(path, headers=headers)

    assert response.status_code == 200
    assert len(seen) == 1
    # The laptop still authenticates itself to the Pi with its own key, never the headset's.
    assert seen[0][protocol.AUTH_HEADER.lower()] == "laptop-pi-secret"


def test_estop_reset_still_requires_quest_key():
    def pi(_: httpx.Request) -> httpx.Response:
        raise AssertionError("unauthenticated reset reached the Pi")

    client, _, _ = make_client(pi)
    response = client.post(protocol.PATH_EMERGENCY_RESET, headers={protocol.AUTH_HEADER: ""})
    assert response.status_code == 401


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

    response = client.post(protocol.PATH_MOTION, json={"forward": 0, "strafe": 0, "turn": 0})
    assert response.status_code == 200
    assert len(events) == 2
    assert "motion stopped" in events[1]["description"]


def test_motion_audit_row_contains_metadata_result_and_latency():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "accepted"})

    client, _, events = make_client(pi)
    client.post(protocol.PATH_MOTION,
                json={"forward": 0.5, "strafe": 0, "turn": 0},
                headers={protocol.SOURCE_HEADER: "voice", protocol.COMMAND_ID_HEADER: "voice-1"})
    assert events[0]["source"] == "voice"
    assert events[0]["command_id"] == "voice-1"
    assert events[0]["result_code"] == 200
    assert events[0]["latency_ms"] >= 0


def test_stop_resets_motion_audit_state_for_next_drive():
    def pi(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "accepted"})

    client, _, events = make_client(pi)
    motion = {"forward": 0.5, "strafe": 0, "turn": 0}
    assert client.post(protocol.PATH_MOTION, json=motion).status_code == 200
    assert client.post(protocol.PATH_STOP).status_code == 200
    assert client.post(protocol.PATH_MOTION, json=motion).status_code == 200

    assert sum("motion started" in event["description"] for event in events) == 2
    assert sum("Quest stop" in event["description"] for event in events) == 1


def test_pi_down_returns_503_instead_of_leaking_transport_error():
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Pi is offline")

    client, _, events = make_client(down)
    response = client.post(
        protocol.PATH_MOTION,
        json={"forward": 1, "strafe": 0, "turn": 0},
    )

    assert response.status_code == 503
    assert "offline" in response.json().get("message", "").lower()
    assert "Pi is offline" not in response.text
    assert events[-1]["event_type"] == "robot_command"
    assert "HTTP 503" in events[-1]["description"]


def test_stop_uses_priority_lane_while_motion_is_in_flight():
    motion_started = threading.Event()
    release_motion = threading.Event()
    motion_result: Dict[str, Any] = {}

    def normal_pi(request: httpx.Request) -> httpx.Response:
        assert request.url.path == protocol.PATH_MOTION
        motion_started.set()
        release_motion.wait(2.0)
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
    assert motion_result["response"].status_code == 409
    assert motion_result["response"].json()["status"] == "rejected"
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
    assert sum("Quest stop" in event["description"] for event in events) == 3
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

    def normal_pi(request: httpx.Request) -> httpx.Response:
        # The normal lane must only ever see the motion request; an e-stop here would fail.
        assert request.url.path == protocol.PATH_MOTION
        motion_started.set()
        release_motion.wait(2.0)
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
    assert motion_result["response"].status_code == 409
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
    payloads = {protocol.PATH_MOTION_DISCRETE: DISCRETE,
                protocol.PATH_POSTURE: {"height": "medium", "roll": 0, "pitch": 0, "yaw": 0}}
    for path in (protocol.PATH_STOP, protocol.PATH_EMERGENCY_STOP, protocol.PATH_EMERGENCY_RESET,
                 protocol.PATH_RESET, protocol.PATH_POSTURE, protocol.PATH_MOTION_DISCRETE):
        assert client.post(path, json=payloads.get(path, {})).status_code == 200

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
    assert sum("emergency_stop; HTTP" in d for d in descriptions) == 3
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
        if ender == protocol.PATH_EMERGENCY_STOP:
            assert client.post(protocol.PATH_EMERGENCY_RESET).status_code == 200
        assert client.post(protocol.PATH_MOTION_DISCRETE, json=DISCRETE).status_code == 200
        assert sum("motion started" in e["description"] for e in events) == 2, ender


# ---------------------------------------------------------------- continuous-motion window

import time  # noqa: E402

from backend.robot.relay_router import continuous_window_s  # noqa: E402

MOVE = {"forward": 0.5, "strafe": 0, "turn": 0}


class _Pi:
    """Fake Pi recording motion repeats and stops with monotonic timestamps."""

    def __init__(self, motion_status=200, fail_repeats_after=None):
        self.motions: List[float] = []
        self.motion_metadata: List[tuple[int, str]] = []
        self.stops: List[float] = []
        self.motion_status = motion_status
        self.fail_repeats_after = fail_repeats_after

    def normal(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == protocol.PATH_MOTION
        self.motions.append(time.monotonic())
        self.motion_metadata.append((int(request.headers[protocol.SEQ_HEADER]),
                                     request.headers[protocol.SENT_AT_HEADER]))
        if self.fail_repeats_after is not None and len(self.motions) > self.fail_repeats_after:
            raise httpx.ConnectError("pi unreachable")
        return httpx.Response(self.motion_status, json={"status": "accepted"})

    def priority(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == protocol.PATH_STOP
        self.stops.append(time.monotonic())
        return httpx.Response(200, json={"status": "stop_dispatched"})


def _wait_for(predicate, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_window_default_is_one_second_and_clamped(monkeypatch):
    monkeypatch.delenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", raising=False)
    assert continuous_window_s() == 1.0
    for raw, expected in [("0.5", 0.5), ("0.05", 0.2), ("-3", 0.2), ("9", 2.0), ("2", 2.0),
                          ("abc", 1.0), ("", 1.0), ("nan", 1.0), ("inf", 1.0)]:
        monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", raw)
        assert continuous_window_s() == expected, raw


def test_quest_sends_one_motion_then_nothing_stop_comes_after_one_second(monkeypatch):
    monkeypatch.delenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", raising=False)
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "true")
    pi = _Pi()
    client, _, _ = make_client(pi.normal, stop_handler=pi.priority)
    with client:
        sent = time.monotonic()
        assert client.post(protocol.PATH_MOTION, json=MOVE).status_code == 200
        assert _wait_for(lambda: pi.stops, 2.5), "no stop was sent"
    elapsed = pi.stops[0] - sent
    assert 0.9 <= elapsed < 1.5, elapsed             # ~1.0 s, not 2.0
    assert len(pi.motions) >= 3                       # it kept repeating until then
    assert len(pi.stops) == 1
    sequences = [seq for seq, _ in pi.motion_metadata]
    assert sequences == sorted(set(sequences))
    assert all(sent_at for _, sent_at in pi.motion_metadata)


def test_env_override_changes_the_stop_time(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", "0.5")
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "true")
    pi = _Pi()
    client, _, _ = make_client(pi.normal, stop_handler=pi.priority)
    with client:
        sent = time.monotonic()
        client.post(protocol.PATH_MOTION, json=MOVE)
        assert _wait_for(lambda: pi.stops, 2.0)
    assert 0.4 <= pi.stops[0] - sent < 0.9


def test_invalid_env_uses_the_default_window(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", "not-a-number")
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "true")
    pi = _Pi()
    client, _, _ = make_client(pi.normal, stop_handler=pi.priority)
    with client:
        sent = time.monotonic()
        client.post(protocol.PATH_MOTION, json=MOVE)
        assert _wait_for(lambda: pi.stops, 2.5)
    assert 0.9 <= pi.stops[0] - sent < 1.5


def test_failed_repeat_request_still_sends_stop(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", "2.0")   # window would not have elapsed
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "true")
    pi = _Pi(fail_repeats_after=1)       # the first (Quest) request works; the repeats raise
    client, _, _ = make_client(pi.normal, stop_handler=pi.priority)
    with client:
        sent = time.monotonic()
        client.post(protocol.PATH_MOTION, json=MOVE)
        assert _wait_for(lambda: pi.stops, 1.5), "repeat failure did not stop the robot"
    assert pi.stops[0] - sent < 1.0       # well before the 2.0 s window
    assert len(pi.stops) == 1


def test_non_2xx_repeat_response_also_stops(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", "2.0")
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "true")
    pi = _Pi()
    statuses = iter([200] + [423] * 50)
    def normal(request: httpx.Request) -> httpx.Response:
        pi.motions.append(time.monotonic())
        return httpx.Response(next(statuses), json={"detail": "x"})
    client, _, _ = make_client(normal, stop_handler=pi.priority)
    with client:
        client.post(protocol.PATH_MOTION, json=MOVE)
        assert _wait_for(lambda: pi.stops, 1.5)


def test_quest_stop_cancels_the_loop_immediately_and_sends_exactly_one_stop(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", "1.0")
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "true")
    pi = _Pi()
    client, _, _ = make_client(pi.normal, stop_handler=pi.priority)
    with client:
        client.post(protocol.PATH_MOTION, json=MOVE)
        time.sleep(0.3)
        assert client.post(protocol.PATH_STOP).status_code == 200
        repeats_at_stop = len(pi.motions)
        time.sleep(1.4)                  # past where the window would have ended
    assert len(pi.motions) == repeats_at_stop     # no repeats after /stop
    assert len(pi.stops) == 1                     # only the Quest's stop, none from the loop


def test_a_newer_quest_command_replaces_the_loop_without_a_stop(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", "0.6")
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "true")
    pi = _Pi()
    client, _, _ = make_client(pi.normal, stop_handler=pi.priority)
    with client:
        t0 = time.monotonic()
        client.post(protocol.PATH_MOTION, json=MOVE)
        time.sleep(0.4)
        client.post(protocol.PATH_MOTION, json=MOVE)    # refreshes the window
        time.sleep(0.4)                                 # 0.8 s after the first: old window would have ended
        assert pi.stops == []
        assert _wait_for(lambda: pi.stops, 1.5)
    assert pi.stops[0] - t0 >= 0.95                     # ~0.4 + 0.6 after the second command


def test_continuous_motion_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "false")
    pi = _Pi()
    client, _, _ = make_client(pi.normal, stop_handler=pi.priority)
    with client:
        client.post(protocol.PATH_MOTION, json=MOVE)
        time.sleep(0.6)
    assert len(pi.motions) == 1 and pi.stops == []


# ---------------------------------------------------------------- stop-class commands cancel the loop first

def _run_stop_class_while_repeating(monkeypatch, path, pi_status):
    """A repeat loop is running; `path` is sent and the Pi answers `pi_status` (or is unreachable when
    None). Returns (motions_at_command, motions_after_waiting, response_status)."""
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION_WINDOW_S", "2.0")
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "true")
    motions: List[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == protocol.PATH_MOTION:
            motions.append(time.monotonic())
            return httpx.Response(200, json={"status": "accepted"})
        if pi_status is None:
            raise httpx.ConnectError("pi unreachable")
        return httpx.Response(pi_status, json={"detail": "pi error"})

    client, _, _ = make_client(respond, stop_handler=respond)
    with client:
        assert client.post(protocol.PATH_MOTION, json=MOVE).status_code == 200
        assert _wait_for(lambda: len(motions) >= 3, 2.0), "repeat loop never started"
        status = client.post(path).status_code
        at_command = len(motions)
        time.sleep(0.8)                  # four repeat intervals
        return at_command, len(motions), status


def test_emergency_stop_cancels_repeat_even_when_the_pi_returns_503(monkeypatch):
    at_command, after, status = _run_stop_class_while_repeating(monkeypatch, protocol.PATH_EMERGENCY_STOP, 503)
    assert status == 503
    assert after == at_command, "motion kept being re-sent after the e-stop"


def test_emergency_stop_cancels_repeat_when_the_pi_is_unreachable(monkeypatch):
    at_command, after, status = _run_stop_class_while_repeating(monkeypatch, protocol.PATH_EMERGENCY_STOP, None)
    assert status == 503                 # the relay's own "Pi unavailable"
    assert after == at_command


def test_reset_cancels_repeat_even_when_the_pi_returns_503(monkeypatch):
    at_command, after, status = _run_stop_class_while_repeating(monkeypatch, protocol.PATH_RESET, 503)
    assert status == 503
    assert after == at_command, "motion kept being re-sent after a reset"


def test_stop_still_cancels_repeat_when_the_pi_returns_503(monkeypatch):
    at_command, after, status = _run_stop_class_while_repeating(monkeypatch, protocol.PATH_STOP, 503)
    assert status == 503
    assert after == at_command
