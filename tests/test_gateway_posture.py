import asyncio
import json

import httpx
import pytest

from backend.gateway import CommandGateway
from backend.robot.config import RobotConfig
from backend.robot.interface import MotionCommand, RobotMode
from backend.robot.remote import RemoteRobotClient
from backend.robot.simulator import RobotSimulator


@pytest.mark.parametrize('source', ['voice', 'llm', 'emergency'])
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


@pytest.mark.parametrize('source', ['hand', 'voice', 'llm'])
def test_measured_imu_does_not_block_commanded_neutral_posture(source):
    from tests.test_robot_remote import FakePi
    from backend.robot import protocol

    class MeasuredPi(FakePi):
        def __call__(self, request):
            response = super().__call__(request)
            if request.url.path == protocol.PATH_TELEMETRY:
                payload = response.json()
                payload['control_state']['posture'] = {
                    'height': 'medium', 'pitch': -1.16, 'roll': .35, 'yaw': -26.87}
                return httpx.Response(200, json=payload)
            return response

    async def run():
        robot = RemoteRobotClient(RobotConfig(mode=RobotMode.REMOTE),
                                  transport=httpx.MockTransport(MeasuredPi()))
        try:
            gateway = CommandGateway(robot)
            gateway.posture_settle_s = .001
            await gateway.status(source=source)
            assert robot.get_control_state().posture.pitch == -1.16
            assert (await gateway.motion(MotionCommand(forward=.3), source=source)).status == 'ACCEPTED'
            assert (await gateway.set_posture({'height': 'medium', 'pitch': -8}, source=source)).status == 'ACCEPTED'
            await gateway.status(source=source)
            assert (await gateway.motion(MotionCommand(forward=.3), source=source)).status == ('ACCEPTED' if source == 'hand' else 'INVALID')
            assert (await gateway.level_posture(source=source)).status == 'ACCEPTED'
            await gateway.status(source=source)
            assert (await gateway.motion(MotionCommand(forward=.3), source=source)).status == 'ACCEPTED'
            assert (await gateway.set_posture({'height': 'medium', 'yaw': 5}, source=source)).status == 'ACCEPTED'
            assert (await gateway.motion(MotionCommand(forward=.3), source=source)).status == 'ACCEPTED'
        finally:
            robot.close()
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['analog', 'discrete', 'bounded', 'turn'])
def test_hand_auto_level_order_and_settle(kind, caplog):
    async def run():
        events = []
        class RecordingSimulator(RobotSimulator):
            def stop(self):
                events.append('stop')
                return super().stop()
            def set_posture(self, posture):
                events.append(('posture', posture.height.value, posture.pitch))
                return super().set_posture(posture)
            def move_analog(self, command):
                events.append('motion')
                return super().move_analog(command)
            def move_discrete(self, direction, intensity=None):
                events.append('motion')
                return super().move_discrete(direction, intensity)
        sim = RecordingSimulator()
        gateway = CommandGateway(sim)
        gateway.posture_settle_s = .02
        assert (await gateway.set_posture({'height': 'low', 'pitch': -8}, source='llm')).status == 'ACCEPTED'
        sim.moving = True
        events.clear()
        started = asyncio.get_running_loop().time()
        commands = {
            'analog': lambda: gateway.motion(MotionCommand(forward=.3), source='hand'),
            'discrete': lambda: gateway.move_discrete('forward', source='hand'),
            'bounded': lambda: gateway.run_bounded('forward', .01, .3, source='hand'),
            'turn': lambda: gateway.run_turn(1, source='hand'),
        }
        # Bounded/turn operations return their final stop result by contract.
        expected = 'STOP_DISPATCHED' if kind in {'bounded', 'turn'} else 'ACCEPTED'
        assert (await commands[kind]()).status == expected
        assert asyncio.get_running_loop().time() - started >= .02
        assert events[:3] == ['stop', ('posture', 'medium', 0), 'motion']
        assert gateway._commanded_posture.pitch == 0
        assert gateway._commanded_posture.height.value == 'medium'
    with caplog.at_level('INFO'):
        asyncio.run(run())
    assert 'auto-levelled before hand motion' in caplog.text


def test_zero_stick_and_estop_do_not_level():
    async def run():
        sim = RobotSimulator()
        gateway = CommandGateway(sim)
        gateway.posture_settle_s = .001
        await gateway.set_posture({'height': 'low', 'pitch': -8}, source='llm')
        assert (await gateway.motion(MotionCommand(), source='hand')).status == 'ACCEPTED'
        assert sim.get_control_state().posture.pitch == -8
        await gateway.emergency_stop(source='hand')
        for command in [lambda: gateway.motion(MotionCommand(forward=.3), source='hand'),
                        lambda: gateway.move_discrete('forward', source='hand'),
                        lambda: gateway.run_bounded('forward', .1, .3, source='hand'),
                        lambda: gateway.run_turn(1, source='hand')]:
            assert (await command()).status == 'ESTOP_ACTIVE'
            assert gateway._commanded_posture.pitch == -8
        assert (await gateway.stop(source='hand')).status in {'ACCEPTED', 'STOP_DISPATCHED'}
        assert gateway._commanded_posture.pitch == -8
    asyncio.run(run())


@pytest.mark.parametrize('emergency', [False, True])
def test_stop_interrupts_auto_level_settling_without_motion(emergency):
    async def run():
        import threading
        level_sent = threading.Event()
        class ObservedSimulator(RobotSimulator):
            def set_posture(self, posture):
                result = super().set_posture(posture)
                if posture.pitch == 0:
                    level_sent.set()
                return result
        sim = ObservedSimulator()
        gateway = CommandGateway(sim)
        gateway.posture_settle_s = .001
        await gateway.set_posture({'height': 'low', 'pitch': -8}, source='llm')
        gateway.posture_settle_s = 5
        move = asyncio.create_task(gateway.motion(MotionCommand(forward=.3), source='hand'))
        assert await asyncio.to_thread(level_sent.wait, 1)
        operation = gateway.emergency_stop if emergency else gateway.stop
        await asyncio.wait_for(operation(source='hand'), .5)
        assert (await move).status == 'CANCELLED'
        assert not sim.moving
    asyncio.run(run())
