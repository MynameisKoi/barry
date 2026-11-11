from __future__ import annotations

import asyncio
import time

import pytest

from backend.gateway import CommandGateway
from backend.robot.config import RobotConfig
from backend.robot.interface import CommandStatus, MotionCommand, RobotMode
from backend.robot.remote import RemoteRobotClient
from backend.robot.server import create_app
from backend.robot.simulator import RobotSimulator
from fastapi.testclient import TestClient


@pytest.fixture
def sim_gateway():
    cfg = RobotConfig(api_key="test-key", watchdog_ms=500, mode=RobotMode.SIMULATOR)
    sim = RobotSimulator(cfg)
    return sim, CommandGateway(sim, mission_check=lambda: True, max_speed=0.6, max_bounded_s=1)


def test_motion_requires_mission_and_has_command_metadata(sim_gateway):
    sim, _ = sim_gateway
    gateway = CommandGateway(sim, mission_check=lambda: False)
    result = asyncio.run(gateway.motion(MotionCommand(forward=0.5), source="hand"))
    assert result.status == "MISSION_REQUIRED"
    assert result.command_id and result.source == "hand"
    assert not sim.moving


def test_speed_and_duration_limits_are_clamped(sim_gateway):
    sim, gateway = sim_gateway
    result = asyncio.run(gateway.run_bounded("forward", 0.25, 1.0, source="voice"))
    assert result.status == "STOP_DISPATCHED"
    assert result.details == {"clamped": True, "seconds": 0.25, "speed": 0.6}
    assert not sim.moving


def test_hand_cancels_bounded_voice_move(sim_gateway):
    sim, gateway = sim_gateway
    async def scenario():
        bounded = asyncio.create_task(gateway.run_bounded("forward", 0.8, 0.5, source="voice"))
        await asyncio.sleep(0.15)
        await gateway.motion(MotionCommand(turn=0.2), source="hand")
        return await bounded
    result = asyncio.run(scenario())
    assert result.status == "CANCELLED"
    assert sim.moving  # the higher-priority hand command took control


def test_voice_command_cancels_llm_operation(sim_gateway):
    sim, gateway = sim_gateway
    async def scenario():
        async def plan():
            await asyncio.sleep(2)
        llm = asyncio.create_task(gateway.run_operation(plan, source="llm"))
        await asyncio.sleep(0.02)
        voice = await gateway.run_bounded("forward", 0.12, 0.4, source="voice")
        return await llm, voice
    llm, voice = asyncio.run(scenario())
    assert llm.status == "CANCELLED"
    assert voice.status == "STOP_DISPATCHED"
    assert not sim.moving


def test_stop_interrupts_bounded_move_within_one_tick(sim_gateway):
    sim, gateway = sim_gateway
    async def scenario():
        bounded = asyncio.create_task(gateway.run_bounded("forward", 2, 0.5, source="llm"))
        await asyncio.sleep(0.05)
        started = time.monotonic()
        stopped = await gateway.stop(source="hand")
        elapsed = time.monotonic() - started
        return await bounded, stopped, elapsed
    bounded, stopped, elapsed = asyncio.run(scenario())
    assert stopped.status in {"STOP_DISPATCHED", "ACCEPTED"}
    assert bounded.status == "CANCELLED"
    assert elapsed <= 0.1
    assert not sim.moving


def test_remote_unavailable_reports_offline_without_queue():
    from httpx import MockTransport, Response, ConnectError
    def offline(request):
        raise ConnectError("offline", request=request)
    config = RobotConfig(mode=RobotMode.REMOTE, api_key="key", remote_url="http://pi.test")
    remote = RemoteRobotClient(config, transport=MockTransport(offline))
    gateway = CommandGateway(remote, mission_check=lambda: True)
    try:
        result = asyncio.run(gateway.run_bounded("forward", 1, 0.5, source="hand"))
        assert result.status == "OFFLINE"
        assert "not queued" in result.message
        assert not remote.connected
    finally:
        remote.close()
