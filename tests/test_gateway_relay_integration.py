"""Quest v1 contract and safety tests through the shared command gateway."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.gateway.gateway import CommandGateway
from backend.robot import protocol
from backend.robot.config import RobotConfig
from backend.robot.interface import MotionCommand, RobotMode
from backend.robot.relay import RobotApiRelay
from backend.robot.remote import RemoteRobotClient
from backend.robot.relay_router import QuestRelayAuth, create_robot_relay_router

KEY = "quest-key"


def client_for(handler, *, mission=True):
    pi = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE, remote_url="http://pi.test",
                                       api_key="pi-key", remote_timeout_s=1),
                           transport=httpx.MockTransport(handler))
    gateway = CommandGateway(pi, mission_check=lambda: mission, max_bounded_s=0.1)
    events: list[dict[str, Any]] = []
    app = FastAPI()
    app.include_router(create_robot_relay_router(lambda: gateway, lambda **kw: events.append(kw), QuestRelayAuth(KEY)))
    return TestClient(app), gateway, pi, events


def quest_headers(client: TestClient, seq=0):
    sid = client.post(protocol.PATH_SESSION, headers={protocol.AUTH_HEADER: KEY})
    assert sid.status_code == 201
    return {protocol.AUTH_HEADER: KEY, protocol.SESSION_HEADER: sid.json()["session_id"], protocol.SEQ_HEADER: str(seq)}


def test_quest_motion_uses_gateway_owned_pi_session_and_metadata():
    seen = []
    def pi(request):
        seen.append((request.method, request.url.path, dict(request.headers), request.content))
        if request.url.path == protocol.PATH_SESSION:
            return httpx.Response(201, json={"session_id": "pi-owned-by-gateway"})
        return httpx.Response(200, json={"status": "accepted", "command": "motion"})
    client, _, _, _ = client_for(pi)
    headers = quest_headers(client)
    headers.update({protocol.SOURCE_HEADER: "hand", protocol.COMMAND_ID_HEADER: "quest-1"})
    response = client.post(protocol.PATH_MOTION, json={"forward": .4, "strafe": 0, "turn": 0}, headers=headers)
    assert response.status_code == 200
    motion = next(item for item in seen if item[1] == protocol.PATH_MOTION)
    assert motion[2][protocol.SESSION_HEADER.lower()] == "pi-owned-by-gateway"
    assert motion[2][protocol.SOURCE_HEADER.lower()] == "hand"
    assert motion[2][protocol.COMMAND_ID_HEADER.lower()]
    assert KEY not in motion[2].values()


def test_stale_quest_sequence_is_rejected_without_pi_call():
    paths = []
    def pi(request):
        paths.append(request.url.path)
        return httpx.Response(201 if request.url.path == protocol.PATH_SESSION else 200,
                              json={"session_id": "pi-session", "status": "accepted"})
    client, _, _, _ = client_for(pi)
    headers = quest_headers(client)
    assert client.post(protocol.PATH_MOTION, json={"forward": .2}, headers=headers).status_code == 200
    assert client.post(protocol.PATH_MOTION, json={"forward": .2}, headers=headers).status_code == 409
    assert paths.count(protocol.PATH_MOTION) == 1


def test_quest_session_delete_stops_motion_and_invalidates_the_session(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "false")
    paths = []
    def pi(request):
        paths.append(request.url.path)
        if request.url.path == protocol.PATH_SESSION:
            return httpx.Response(201, json={"session_id": "pi-session"})
        return httpx.Response(200, json={"status": "accepted", "command": "motion"})
    client, _, _, _ = client_for(pi)
    headers = quest_headers(client)
    assert client.post(protocol.PATH_MOTION, json={"forward": .2}, headers=headers).status_code == 200
    delete_headers = dict(headers); delete_headers[protocol.SEQ_HEADER] = "1"
    assert client.delete(protocol.PATH_SESSION, headers=delete_headers).status_code == 200
    stale = dict(headers); stale[protocol.SEQ_HEADER] = "2"
    assert client.post(protocol.PATH_MOTION, json={"forward": .2}, headers=stale).status_code == 409
    assert paths.count(protocol.PATH_STOP) >= 1


def test_motion_requires_mission_then_succeeds_after_start():
    active = False
    def pi(request):
        return httpx.Response(201 if request.url.path == protocol.PATH_SESSION else 200,
                              json={"session_id": "pi-session", "status": "accepted", "command": "motion"})
    robot = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE, remote_url="http://pi.test", api_key="pi-key"),
                              transport=httpx.MockTransport(pi))
    gateway = CommandGateway(robot, mission_check=lambda: active)
    app = FastAPI(); app.include_router(create_robot_relay_router(lambda: gateway, lambda **kw: None, QuestRelayAuth(KEY)))
    client = TestClient(app); headers = quest_headers(client)
    assert client.post(protocol.PATH_MOTION, json={"forward": .2}, headers=headers).status_code == 403
    active = True
    headers[protocol.SEQ_HEADER] = "1"
    assert client.post(protocol.PATH_MOTION, json={"forward": .2}, headers=headers).status_code == 200


@pytest.mark.parametrize("path", [protocol.PATH_STOP, protocol.PATH_EMERGENCY_STOP])
@pytest.mark.parametrize("key", [None, "wrong"])
def test_priority_stop_and_estop_are_keyless(path, key):
    calls = []
    def pi(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"status": "stop_dispatched", "command": "stop"})
    client, _, _, _ = client_for(pi)
    headers = {protocol.AUTH_HEADER: key} if key else {}
    response = client.post(path, headers=headers)
    assert response.status_code == 200
    assert protocol.PATH_SESSION not in calls


def test_quest_motion_preempts_llm_and_hand_hold_blocks_llm(monkeypatch):
    monkeypatch.setenv("GATEWAY_HAND_HOLD_S", "2")
    def pi(request):
        return httpx.Response(201 if request.url.path == protocol.PATH_SESSION else 200,
                              json={"session_id": "pi-session", "status": "accepted", "command": "motion"})
    client, gateway, _, _ = client_for(pi)
    headers = quest_headers(client)
    started = threading.Event(); cancelled = threading.Event()
    async def llm_work():
        started.set()
        try: await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set(); raise
    async def run_llm(): return await gateway.run_operation(llm_work, source="llm")
    import anyio
    async def scenario():
        task = asyncio.create_task(run_llm()); await asyncio.sleep(.02)
        response = await anyio.to_thread.run_sync(lambda: client.post(protocol.PATH_MOTION,
            json={"forward": .2}, headers=headers))
        result = await task
        return response, result
    response, result = anyio.run(scenario)
    assert started.is_set() and cancelled.is_set()
    assert response.status_code == 200
    assert result.status == "CANCELLED"
    assert gateway._last_hand_at > 0


def test_audit_logs_motion_transitions_and_each_stop_action(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "false")
    def pi(request):
        return httpx.Response(201 if request.url.path == protocol.PATH_SESSION else 200,
                              json={"session_id": "pi-session", "status": "accepted", "command": "motion"})
    client, _, _, events = client_for(pi)
    headers = quest_headers(client)
    client.post(protocol.PATH_MOTION, json={"forward": .3}, headers=headers)
    headers[protocol.SEQ_HEADER] = "1"
    client.post(protocol.PATH_MOTION, json={"forward": .4}, headers=headers)
    client.post(protocol.PATH_STOP)
    client.post(protocol.PATH_STOP)
    assert len([event for event in events if "motion started" in event["description"]]) == 1
    assert len([event for event in events if "Quest stop;" in event["description"]]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("override, expected", [(None, .3), ("0.8", .8), ("0.01", .1), ("9", 2.0)])
async def test_relay_priority_lane_uses_configured_short_timeout(monkeypatch, override, expected):
    if override is None: monkeypatch.delenv("ROBOT_STOP_TIMEOUT_S", raising=False)
    else: monkeypatch.setenv("ROBOT_STOP_TIMEOUT_S", override)
    seen = []
    def pi(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"status": "stop_dispatched", "command": "stop"})
    relay = RobotApiRelay(RobotConfig(mode=RobotMode.REMOTE, remote_url="http://pi.test", api_key="pi-key"),
                          transport=httpx.MockTransport(pi))
    scope = {"type": "http", "method": "POST", "path": protocol.PATH_STOP,
             "headers": [], "query_string": b"", "server": ("test", 80), "client": ("test", 1),
             "scheme": "http", "http_version": "1.1"}
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    await relay.forward(Request(scope, receive), protocol.PATH_STOP, priority_stop=True)
    assert seen == [expected]
    await relay.aclose()


@pytest.mark.parametrize("override, expected", [(None, .3), ("0.7", .7), ("0.01", .1), ("3", 2.0)])
def test_remote_priority_lane_uses_configured_short_timeout(monkeypatch, override, expected):
    if override is None: monkeypatch.delenv("ROBOT_STOP_TIMEOUT_S", raising=False)
    else: monkeypatch.setenv("ROBOT_STOP_TIMEOUT_S", override)
    seen = []
    def pi(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"status": "stop_dispatched", "command": "stop"})
    robot = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE, remote_url="http://pi.test", api_key="pi-key",
                                          remote_timeout_s=2.5), transport=httpx.MockTransport(pi))
    robot.stop()
    assert seen == [expected]


@pytest.mark.asyncio
async def test_llm_bounded_move_uses_gateway_remote_lease_and_source_metadata():
    seen = []
    def pi(request):
        seen.append(request)
        if request.url.path == protocol.PATH_SESSION:
            return httpx.Response(201, json={"session_id": "one-gateway-session"})
        if request.url.path == protocol.PATH_STOP:
            return httpx.Response(200, json={"status": "stop_dispatched", "command": "stop"})
        return httpx.Response(200, json={"status": "accepted", "command": "motion"})
    robot = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE, remote_url="http://pi.test", api_key="pi-key"),
                              transport=httpx.MockTransport(pi))
    from backend.gateway.gateway import CommandGateway
    gateway = CommandGateway(robot, mission_check=lambda: True, max_bounded_s=.1)
    result = await gateway.run_bounded("forward", .05, .3, source="llm")
    move = next(request for request in seen if request.url.path == protocol.PATH_MOTION)
    assert result.status == "STOP_DISPATCHED"
    assert move.headers[protocol.SOURCE_HEADER] == "llm"
    assert move.headers[protocol.COMMAND_ID_HEADER]
    assert move.headers[protocol.SESSION_HEADER] == "one-gateway-session"


@pytest.mark.asyncio
async def test_hand_hold_blocks_llm_until_window_expires():
    def pi(request):
        return httpx.Response(201 if request.url.path == protocol.PATH_SESSION else 200,
                              json={"session_id": "pi-session", "status": "accepted", "command": "motion"})
    client, gateway, _, _ = client_for(pi)
    headers = quest_headers(client)
    response = client.post(protocol.PATH_MOTION, json={"forward": .2}, headers=headers)
    assert response.status_code == 200
    blocked = await gateway.run_bounded("forward", .01, .2, source="llm")
    assert blocked.status == "BUSY"
    gateway.hand_hold_s = .01
    await asyncio.sleep(.02)
    expired = await gateway.run_bounded("forward", .01, .2, source="llm")
    assert expired.status in {"ACCEPTED", "STOP_DISPATCHED"}


def test_priority_stop_reaches_pi_while_gateway_motion_is_blocked(monkeypatch):
    monkeypatch.setenv("ROBOT_CONTINUOUS_MOTION", "false")
    motion_started, release_motion, stop_seen = threading.Event(), threading.Event(), threading.Event()
    def pi(request):
        if request.url.path == protocol.PATH_SESSION:
            return httpx.Response(201, json={"session_id": "pi-session"})
        if request.url.path == protocol.PATH_MOTION:
            motion_started.set(); release_motion.wait(2)
            return httpx.Response(200, json={"status": "accepted", "command": "motion"})
        if request.url.path == protocol.PATH_STOP:
            stop_seen.set()
            return httpx.Response(200, json={"status": "stop_dispatched", "command": "stop"})
        return httpx.Response(200, json={"status": "accepted", "command": "unknown"})
    client, _, _, _ = client_for(pi)
    headers = quest_headers(client)
    worker = threading.Thread(target=lambda: client.post(protocol.PATH_MOTION,
        json={"forward": .4}, headers=headers))
    worker.start()
    assert motion_started.wait(1)
    response = client.post(protocol.PATH_STOP)
    assert response.status_code == 200 and stop_seen.is_set()
    release_motion.set(); worker.join(1)
    assert not worker.is_alive()
