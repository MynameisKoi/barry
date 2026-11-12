import asyncio
import json

import httpx
import pytest

from backend.gateway import CommandGateway
from backend.robot.config import RobotConfig
from backend.robot.interface import MotionCommand, RobotMode
from backend.robot.remote import RemoteRobotClient
from backend.robot.simulator import RobotSimulator


@pytest.mark.parametrize('source', ['hand', 'voice', 'llm', 'emergency'])
def test_posture_limits_and_level(source):
    async def run():
        sim = RobotSimulator()
        gateway = CommandGateway(sim)
        gateway.posture_settle_s = 0.001
        for pitch in [-10.1, 10.1, float('nan')]:
            assert (await gateway.set_posture({'height': 'low', 'pitch': pitch}, source=source)).status == 'INVALID'
        assert (await gateway.set_posture({'height': 'low', 'pitch': -10}, source=source)).status == 'ACCEPTED'
        assert sim.get_control_state().posture.pitch == -10
        assert sim.get_control_state().posture.height.value == 'low'
        for command in [lambda: gateway.motion(MotionCommand(forward=.3), source=source),
                        lambda: gateway.move_discrete('forward', source=source),
                        lambda: gateway.run_bounded('forward', .01, .3, source=source),
                        lambda: gateway.run_turn(10, source=source)]:
            assert (await command()).status == 'INVALID'
            assert not sim.moving
        assert (await gateway.level_posture(source=source)).status == 'ACCEPTED'
        assert sim.get_control_state().posture.pitch == 0
        assert sim.get_control_state().posture.height.value == 'medium'
        assert (await gateway.motion(MotionCommand(forward=.3), source=source)).status == 'ACCEPTED'
    asyncio.run(run())


@pytest.mark.parametrize('height', ['low', 'medium', 'high'])
@pytest.mark.parametrize('pitch', [-10, 10])
def test_remote_posture_wire(height, pitch):
    seen = []
    def pi(request):
        if request.url.path.endswith('/session'):
            return httpx.Response(201, json={'session_id': 'test-session'})
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={'status': 'accepted', 'command': 'posture', 'message': 'ok'})
    robot = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE), transport=httpx.MockTransport(pi))
    gateway = CommandGateway(robot)
    gateway.posture_settle_s = .001
    result = asyncio.run(gateway.set_posture({'height': height, 'pitch': pitch}, source='llm'))
    assert result.status == 'ACCEPTED'
    assert seen == [('/api/v1/posture', {'height': height, 'roll': 0, 'pitch': pitch, 'yaw': 0})]
    assert robot.get_control_state().posture.pitch == pitch
    robot.close()
