from __future__ import annotations

import asyncio
import math
import time

import pytest

from backend.gateway import CommandGateway
from backend.robot.config import RobotConfig
from backend.robot.interface import MotionCommand, Orientation, RobotMode, Telemetry
from backend.robot.remote import RemoteRobotClient
from backend.robot.simulator import RobotSimulator


@pytest.fixture
def sim_gateway():
    cfg = RobotConfig(api_key="test-key", watchdog_ms=500, mode=RobotMode.SIMULATOR)
    sim = RobotSimulator(cfg)
    return sim, CommandGateway(sim, mission_check=lambda: True, max_speed=0.6, max_bounded_s=1)


def test_motion_requires_mission_only_for_voice_and_llm_not_hand(monkeypatch, sim_gateway):
    monkeypatch.setenv("REQUIRE_ACTIVE_MISSION", "true")
    sim, _ = sim_gateway
    gateway = CommandGateway(sim, mission_check=lambda: False)
    # Voice and LLM require an active mission when REQUIRE_ACTIVE_MISSION is enabled
    voice_res = asyncio.run(gateway.motion(MotionCommand(forward=0.5), source="voice"))
    assert voice_res.status == "MISSION_REQUIRED"
    assert voice_res.command_id and voice_res.source == "voice"
    assert not sim.moving

    llm_res = asyncio.run(gateway.run_bounded("forward", 0.2, 0.4, source="llm"))
    assert llm_res.status == "MISSION_REQUIRED"
    assert llm_res.command_id and llm_res.source == "llm"
    assert not sim.moving

    # Hand control moves directly without requiring an active mission
    hand_res = asyncio.run(gateway.motion(MotionCommand(forward=0.5), source="hand"))
    assert hand_res.status == "ACCEPTED"
    assert hand_res.command_id and hand_res.source == "hand"
    assert sim.moving

    # When REQUIRE_ACTIVE_MISSION is false, voice and llm also move freely without an active mission
    monkeypatch.setenv("REQUIRE_ACTIVE_MISSION", "false")
    gateway._last_hand_at = 0.0
    voice_unlocked = asyncio.run(gateway.motion(MotionCommand(forward=0.5), source="voice"))
    assert voice_unlocked.status == "ACCEPTED"


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
    from httpx import MockTransport, ConnectError
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


def test_run_turn_positive_is_right_for_yaw_and_calibrated_paths(sim_gateway):
    sim, gateway = sim_gateway
    seen = []
    original_move = sim.move_analog
    def record(command):
        seen.append(command.turn)
        return original_move(command)
    sim.move_analog = record
    yaw_values = iter([0, 0, 90])
    sim.read_telemetry = lambda: Telemetry(orientation=Orientation(yaw=next(yaw_values)))
    yaw_result = asyncio.run(gateway.run_turn(90, source="voice"))
    assert yaw_result.status == "STOP_DISPATCHED"
    assert seen and seen[0] < 0

    seen.clear()
    sim.read_telemetry = lambda: Telemetry(orientation=Orientation(yaw=None))
    gateway.turn_deg_per_s = 1000
    calibrated_result = asyncio.run(gateway.run_turn(90, source="voice"))
    assert calibrated_result.status == "STOP_DISPATCHED"
    assert seen and seen[0] < 0


@pytest.mark.parametrize("degrees", [0, math.nan, math.inf, -math.inf])
def test_run_turn_rejects_invalid_angles(degrees, sim_gateway):
    sim, gateway = sim_gateway
    result = asyncio.run(gateway.run_turn(degrees, source="voice"))
    assert result.status == "INVALID"
    assert not sim.moving


def test_lower_priority_task_is_busy_and_same_source_replaces(sim_gateway):
    _, gateway = sim_gateway
    async def scenario():
        async def idle():
            await asyncio.sleep(2)
        hand = asyncio.create_task(gateway.run_operation(idle, source="hand"))
        await asyncio.sleep(0.02)
        busy = await gateway.run_operation(lambda: None, source="llm")
        replacement = asyncio.create_task(gateway.run_operation(lambda: None, source="hand"))
        await asyncio.sleep(0.02)
        return await hand, busy, await replacement
    old, busy, replacement = asyncio.run(scenario())
    assert old.status == "CANCELLED"
    assert busy.status == "BUSY"
    assert replacement.status == "ACCEPTED"


def test_hand_hold_window_expires(monkeypatch, sim_gateway):
    sim, _ = sim_gateway
    monkeypatch.setenv("GATEWAY_HAND_HOLD_S", "0.02")
    gateway = CommandGateway(sim, mission_check=lambda: True)
    async def scenario():
        await gateway.stop(source="hand")
        blocked = await gateway.run_bounded("forward", 0.01, 0.2, source="voice")
        await asyncio.sleep(0.03)
        resumed = await gateway.run_bounded("forward", 0.01, 0.2, source="voice")
        return blocked, resumed
    blocked, resumed = asyncio.run(scenario())
    assert blocked.status == "BUSY"
    assert resumed.status == "STOP_DISPATCHED"


def test_cancelling_bounded_move_sends_stop(sim_gateway):
    sim, gateway = sim_gateway
    stops = []
    original_stop = sim.stop
    def record_stop():
        stops.append(True)
        return original_stop()
    sim.stop = record_stop
    async def scenario():
        running = asyncio.create_task(gateway.run_bounded("forward", 2, 0.3, source="llm"))
        await asyncio.sleep(0.03)
        await gateway.stop(source="hand")
        return await running
    result = asyncio.run(scenario())
    assert result.status == "CANCELLED"
    assert stops
    assert not sim.moving


def test_emergency_stop_latch_rejects_motion_until_reset(sim_gateway):
    sim, gateway = sim_gateway
    async def scenario():
        latched = await gateway.emergency_stop(source="hand")
        rejected = await gateway.run_bounded("forward", 0.1, 0.2, source="hand")
        reset = await gateway.reset_emergency_stop(source="hand")
        accepted = await gateway.run_bounded("forward", 0.01, 0.2, source="hand")
        return latched, rejected, reset, accepted
    latched, rejected, reset, accepted = asyncio.run(scenario())
    assert latched.status == "ACCEPTED"
    assert rejected.status == "ESTOP_ACTIVE"
    assert reset.status == "ACCEPTED"
    assert accepted.status == "STOP_DISPATCHED"
    assert not sim.emergency_stopped and not sim.moving
