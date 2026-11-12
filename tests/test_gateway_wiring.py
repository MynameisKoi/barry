from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from backend.ai.control.brain import MockBrain
from backend.ai.control.executor import LoopLimits
from backend.ai.control.gateway_adapter import CommandGatewayAdapter, VoiceGatewayAdapter
from backend.ai.control.router import create_llm_router
from backend.gateway.gateway import CommandGateway
from backend.robot.config import RobotConfig
from backend.robot.interface import MotionCommand, RobotMode
from backend.robot.relay_router import QuestRelayAuth
from backend.robot.simulator import RobotSimulator
from backend.voice.router import create_voice_router

KEY = "quest-key"
AUTH = {"X-Robot-Key": KEY}


def setup_app(sim, *, brain=None, transcriber=None, mission=True, max_bounded_s=0.1):
    gateway = CommandGateway(sim, mission_check=lambda: mission, max_bounded_s=max_bounded_s)
    adapter = CommandGatewayAdapter(gateway)
    llm_router = create_llm_router(QuestRelayAuth(KEY), brain or MockBrain(), adapter,
                                   LoopLimits(max_steps=12, max_turns=8, timeout_s=4))
    voice_adapter = VoiceGatewayAdapter(gateway)
    app = FastAPI()
    app.include_router(llm_router)
    if transcriber:
        app.include_router(create_voice_router(QuestRelayAuth(KEY), transcriber,
                                               voice_adapter, llm_router.task_runner))
    return app, gateway, adapter, llm_router.task_runner


@pytest.fixture
def simulator():
    return RobotSimulator(RobotConfig(mode=RobotMode.SIMULATOR))


@pytest.mark.asyncio
async def test_llm_plan_moves_simulator_through_gateway(simulator):
    app, gateway, _, runner = setup_app(simulator)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/llm/plan", json={"goal": "forward 1 second"}, headers=AUTH)
        assert response.status_code == 200
        await runner.wait()
    assert runner.task.state == "done"
    assert runner.task.steps[0].result["source"] == "llm"
    assert simulator.x > 0 and not simulator.moving
    assert await gateway.detections() == []
    assert (await gateway.status())["detector"] == "unavailable"


@pytest.mark.asyncio
async def test_gateway_never_returns_simulated_detection_alerts(simulator):
    from backend.ai.detection import detection_service
    synthetic_alert = detection_service.generate_simulated_detection()
    gateway = CommandGateway(simulator, mission_check=lambda: True)
    assert synthetic_alert not in await gateway.detections()
    assert await gateway.detections() == []


@pytest.mark.asyncio
async def test_gateway_adapter_exposes_style_action_and_status(simulator):
    gateway = CommandGateway(simulator, mission_check=lambda: True)
    adapter = CommandGatewayAdapter(gateway)
    style = await gateway.set_style("low", "walk", source="llm")
    action = await gateway.run_action(10, source="llm")
    status = await adapter.status()
    assert style.status == "ACCEPTED"
    assert action.status == "ACCEPTED"
    assert status["online"] is True
    assert status["detector"] == "unavailable"
    assert status["emergency_stopped"] is False


@pytest.mark.asyncio
async def test_llm_mission_and_estop_results_return_to_brain(simulator):
    simulator.emergency_stop()
    app, _, _, runner = setup_app(simulator)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/llm/plan", json={"goal": "forward 1 second"}, headers=AUTH)
        await runner.wait()
    assert runner.task.steps[0].result["status"] == "ESTOP_ACTIVE"
    assert not simulator.moving

    simulator.reset_emergency_stop()
    app, _, _, runner = setup_app(simulator, mission=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/llm/plan", json={"goal": "forward 1 second"}, headers=AUTH)
        await runner.wait()
    assert runner.task.steps[0].result["status"] == "MISSION_REQUIRED"


class FakeTranscriber:
    def __init__(self, text): self.text = text
    async def transcribe(self, *_): return self.text


@pytest.mark.asyncio
async def test_voice_command_moves_and_unmatched_speech_starts_llm(simulator):
    app, _, _, runner = setup_app(simulator, transcriber=FakeTranscriber("forward 2 seconds"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/voice/command", files={"audio": ("a.wav", b"clip", "audio/wav")}, headers=AUTH)
    assert response.json()["command_result"]["source"] == "voice"
    assert simulator.x > 0 and not simulator.moving

    app, _, _, runner = setup_app(simulator, transcriber=FakeTranscriber("inspect the door"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/voice/command", files={"audio": ("a.wav", b"clip", "audio/wav")}, headers=AUTH)
        await runner.wait()
    assert response.json()["task"]["goal"] == "inspect the door"
    assert runner.task.state == "done"


@pytest.mark.asyncio
async def test_llm_positive_ninety_turns_simulator_right(simulator):
    app, _, _, runner = setup_app(simulator)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/llm/plan", json={"goal": "turn right 90 degrees"}, headers=AUTH)
        await runner.wait()
    assert simulator.heading > 350 or simulator.heading < 0
    assert runner.task.steps[0].args["degrees"] == 90


@pytest.mark.asyncio
async def test_voice_stop_cancels_llm_task_and_stops_robot(simulator):
    app, gateway, _, runner = setup_app(simulator, transcriber=FakeTranscriber("stop"), max_bounded_s=5)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await runner.start("forward 5 seconds")
        deadline = time.monotonic() + 1
        while not simulator.moving and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert simulator.moving
        response = await client.post("/api/v1/voice/command", files={"audio": ("a.wav", b"clip", "audio/wav")}, headers=AUTH)
        await runner.wait()
    assert response.json()["command_result"]["source"] == "voice"
    assert runner.task.state == "cancelled"
    assert not simulator.moving


@pytest.mark.asyncio
async def test_hand_preemption_cancels_llm_task_and_no_later_step(simulator):
    app, gateway, _, runner = setup_app(simulator, max_bounded_s=5)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/llm/plan", json={"goal": "forward 5 seconds then turn right"}, headers=AUTH)
        deadline = time.monotonic() + 1
        while not simulator.moving and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert simulator.moving
        assert gateway._preempt_callbacks
        await gateway.motion(MotionCommand(turn=0.2), source="hand")
        await runner.wait()
    assert runner.task.state == "cancelled"
    assert len(runner.task.steps) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", ["stop", "estop"])
async def test_stop_and_estop_cancel_whole_llm_task(simulator, interrupt):
    app, gateway, _, runner = setup_app(simulator, max_bounded_s=5)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/llm/plan", json={"goal": "forward 5 seconds then turn right"}, headers=AUTH)
        deadline = time.monotonic() + 1
        while not simulator.moving and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert simulator.moving
        if interrupt == "stop":
            await gateway.stop(source="hand")
        else:
            await gateway.emergency_stop(source="hand")
        await runner.wait()
    assert runner.task.state == "cancelled"
    assert len(runner.task.steps) == 1
    assert not simulator.moving
    if interrupt == "estop":
        assert simulator.emergency_stopped


@pytest.mark.asyncio
async def test_remote_mode_adapter_uses_gateway_without_relay_conflict(simulator):
    app, _, _, runner = setup_app(simulator)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/llm/plan", json={"goal": "forward 1 second"}, headers=AUTH)
        await runner.wait()
    assert runner.task.steps[0].result["status"] in {"ACCEPTED", "STOP_DISPATCHED"}
    assert runner.task.steps[0].result["status"] != "CONFLICT_RELAY_ACTIVE"


@pytest.mark.asyncio
async def test_voice_stop_or_estop_reaches_gateway(simulator):
    adapter = VoiceGatewayAdapter(CommandGateway(simulator, mission_check=lambda: True))
    simulator.move_analog(MotionCommand(forward=0.4))
    stopped = await adapter.stop(source="voice")
    assert stopped["status"] == "STOP_DISPATCHED"
    assert not simulator.moving

    simulator.move_analog(MotionCommand(forward=0.4))
    estopped = await adapter.emergency_stop()
    assert estopped["status"] == "ACCEPTED"
    assert simulator.emergency_stopped and not simulator.moving


@pytest.mark.parametrize("path, expected_source", [
    ("/api/v1/motion", "hand"),
    ("/api/v1/motion/discrete", "hand"),
    ("/api/v1/stop", "hand"),
    ("/api/v1/emergency-stop", "emergency"),
])
@pytest.mark.asyncio
async def test_quest_relay_controls_notify_llm_preemption(monkeypatch, path, expected_source):
    import backend.main as main

    notifications = []
    monkeypatch.setenv("QUEST_API_KEY", KEY)
    monkeypatch.setattr(main.app.state, "gateway", type("G", (), {
        "notify_preempt": lambda self, source: notifications.append(source)
    })(), raising=False)
    scope = {
        "type": "http", "method": "POST", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"x-robot-key", KEY.encode())],
        "server": ("test", 80), "client": ("test", 1234), "scheme": "http",
        "http_version": "1.1",
    }
    request = Request(scope)
    response = await main.cancel_llm_for_legacy_hand_controls(request, lambda _: asyncio.sleep(0, result=Response()))
    assert response.status_code == 200
    assert notifications == [expected_source]


@pytest.mark.asyncio
async def test_unauthenticated_quest_relay_does_not_preempt_llm(monkeypatch):
    import backend.main as main

    notifications = []
    monkeypatch.setenv("QUEST_API_KEY", KEY)
    monkeypatch.delenv("QUEST_API_KEY_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.setattr(main.app.state, "gateway", type("G", (), {
        "notify_preempt": lambda self, source: notifications.append(source)
    })(), raising=False)
    path = "/api/v1/motion"
    request = Request({
        "type": "http", "method": "POST", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [], "server": ("test", 80),
        "client": ("test", 1234), "scheme": "http", "http_version": "1.1",
    })
    await main.cancel_llm_for_legacy_hand_controls(request, lambda _: asyncio.sleep(0, result=Response()))
    assert notifications == []
class SlowBrain:
    def ensure_ready(self):
        pass

    async def turn(self, _messages):
        await asyncio.sleep(5)


@pytest.mark.parametrize(("path", "source"), [
    ("/api/v1/stop", "hand"),
    ("/api/v1/emergency-stop", "emergency"),
])
@pytest.mark.asyncio
async def test_unkeyed_priority_stop_routes_cancel_running_llm(monkeypatch, simulator, path, source):
    import backend.main as main

    gateway = CommandGateway(simulator, mission_check=lambda: True)
    adapter = CommandGatewayAdapter(gateway)
    router = create_llm_router(QuestRelayAuth(KEY), SlowBrain(), adapter)
    runner = router.task_runner
    adapter.gateway  # register the runner's preemption hook
    monkeypatch.setattr(main.app.state, "gateway", gateway, raising=False)
    await runner.start("a long running goal")
    await asyncio.sleep(0.02)
    path_bytes = path.encode()
    request = Request({
        "type": "http", "method": "POST", "path": path, "raw_path": path_bytes,
        "query_string": b"", "headers": [], "server": ("test", 80),
        "client": ("test", 1234), "scheme": "http", "http_version": "1.1",
    })

    async def endpoint(_request):
        if source == "hand":
            await gateway.stop(source="hand")
        else:
            await gateway.emergency_stop(source="emergency")
        return Response()

    await main.cancel_llm_for_legacy_hand_controls(request, endpoint)
    await runner.wait()
    assert runner.task.state == "cancelled"


@pytest.mark.asyncio
async def test_mission_start_and_stop_require_quest_key(monkeypatch):
    import backend.main as main
    monkeypatch.setenv("QUEST_API_KEY", "test-quest-key")
    monkeypatch.delenv("QUEST_API_KEY_ALLOW_ANONYMOUS", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        for path, payload in [("/api/mission/start", {"operator_id": "missing", "zone": "test"}),
                              ("/api/mission/stop", {"reason": "test"})]:
            response = await client.post(path, json=payload, headers={"X-Robot-Key": "wrong"})
            assert response.status_code == 401
        response = await client.post("/api/mission/start", json={"operator_id": "missing", "zone": "test"},
                                     headers={"X-Robot-Key": "test-quest-key"})
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_mission_stop_cancels_llm_task_and_stops_robot(monkeypatch, simulator):
    import backend.main as main
    monkeypatch.setenv("QUEST_API_KEY", "test-quest-key")
    gateway = CommandGateway(simulator, mission_check=lambda: True)
    main.app.state.gateway = gateway
    simulator.move_analog(MotionCommand(forward=0.4))
    started = asyncio.Event()
    async def operation():
        started.set()
        await asyncio.sleep(5)
    task = asyncio.create_task(gateway.run_operation(operation, source="llm"))
    await started.wait()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/api/mission/stop", json={"reason": "test"},
                                     headers={"X-Robot-Key": "test-quest-key"})
    result = await task
    assert response.status_code == 200
    assert result.status == "CANCELLED"
    assert not simulator.moving
