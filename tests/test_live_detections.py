"""The background DetectionLoop is the only producer for every consumer."""

import asyncio
import time
import threading

import numpy as np
import pytest

from backend.ai.control.brain import MockBrain
from backend.ai.control.executor import LoopLimits, TaskRunner
from backend.ai.control.gateway_adapter import CommandGatewayAdapter
from backend.ai.detection import AIDetectionService
from backend.ai.vision.detector import COCO_CLASSES, Detector
from backend.camera.interface import Frame
from backend.gateway import CommandGateway
from backend.gateway.detections import AVAILABLE, NO_CAMERA, NO_MODEL, UNAVAILABLE, LiveDetections
from backend.models import DetectionAlert
from backend.robot.config import RobotConfig
from backend.robot.interface import RobotMode
from backend.robot.simulator import RobotSimulator


def run(coro):
    return asyncio.run(coro)


class Camera:
    def __init__(self, *, unavailable=False):
        self.unavailable = unavailable

    def latest_frame(self):
        if self.unavailable:
            raise RuntimeError("camera unavailable")
        return Frame(jpeg=b"frame", timestamp=time.monotonic(), width=64, height=48, sequence=1)


class RepeatingSource:
    name = "test-source"

    def __init__(self, *, frame_age_s=0.0):
        self.frame_age_s = frame_age_s
        self.open_count = 0
        self.close_count = 0
        self.opened = False

    def open(self):
        self.open_count += 1
        self.opened = True

    def read_with_timestamp(self):
        if not self.opened:
            raise RuntimeError("closed")
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        return frame, time.monotonic() - self.frame_age_s

    def read(self):
        return self.read_with_timestamp()[0]

    def close(self):
        self.close_count += 1
        self.opened = False


class CountingDetector:
    available = True

    def __init__(self, *, labels=("chair",), fail=False):
        self.labels = labels
        self.fail = fail
        self.calls = 0
        self.threads = []

    def detect(self, frame, zone="Robot camera"):
        self.calls += 1
        self.threads.append(threading.current_thread())
        if self.fail:
            raise RuntimeError("inference failed")
        assert frame.shape == (48, 64, 3)
        return [DetectionAlert(object=label, confidence=0.876, zone=zone,
                               timestamp="detected-at", bounding_box=[0.1, 0.2, 0.5, 0.6])
                for label in self.labels]


class FakeSession:
    def __init__(self, class_id, score=0.9):
        self.class_id, self.score = class_id, score

    def get_inputs(self):
        return [type("I", (), {"name": "images"})()]

    def run(self, _names, _feed):
        output = np.zeros((1, 84, 1), dtype=np.float32)
        output[0, :4, 0] = (320, 320, 200, 200)
        output[0, 4 + self.class_id, 0] = self.score
        return [output]


def wait_for_result(service, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        loop = service._loop
        if loop is not None and loop.latest()[1] is not None:
            return loop.latest()
        time.sleep(0.005)
    raise AssertionError("background detector did not publish a result")


def make_service(*, detector=None, camera=None, source=None, fps=0.1, max_age_s=2.0):
    detector = detector if detector is not None else CountingDetector()
    camera = camera if camera is not None else Camera()
    source = source if source is not None else RepeatingSource()
    service = AIDetectionService(detector=detector, max_age_s=max_age_s)
    service.attach_source(source, camera=camera, fps=fps)
    return service, detector, source


def test_loop_latest_includes_capture_timestamp_not_inference_completion_time():
    detector = CountingDetector()
    source = RepeatingSource(frame_age_s=0.4)
    service, detector, _ = make_service(detector=detector, source=source)
    try:
        _, frame_timestamp = wait_for_result(service)
        assert time.monotonic() - frame_timestamp >= 0.39
        assert service.status() == AVAILABLE
        result = service.detections()
        assert result[0]["age_s"] >= 0.39
        assert result[0]["label"] == result[0]["object"] == "chair"
        assert result[0]["box"] == result[0]["bounding_box"] == [0.1, 0.2, 0.5, 0.6]
        assert result[0]["simulated"] is False
        assert detector.threads[0] is not threading.current_thread()
    finally:
        service.stop()


def test_detection_loop_starts_once_and_stops_cleanly():
    service, detector, source = make_service(fps=0.1)
    try:
        loop = service._loop
        loop.start()
        wait_for_result(service)
        assert detector.calls == 1
        assert source.open_count == 1
    finally:
        service.stop()
    assert service._loop is None
    assert not source.opened
    assert service.detections() == []


def test_stale_loop_results_are_empty_and_unavailable():
    service, _, _ = make_service(source=RepeatingSource(frame_age_s=2.5), max_age_s=2.0)
    try:
        wait_for_result(service)
        assert service.status() == UNAVAILABLE
        assert service.detections() == []
    finally:
        service.stop()


def test_camera_and_model_statuses_are_preserved():
    service, _, _ = make_service(camera=Camera(unavailable=True))
    try:
        assert service.status() == NO_CAMERA
        assert service.detections() == []
    finally:
        service.stop()

    no_camera = AIDetectionService(detector=CountingDetector())
    assert no_camera.status() == NO_CAMERA

    no_model = AIDetectionService(detector=type("NoModel", (), {"available": False})())
    no_model.attach_source(RepeatingSource(), camera=Camera())
    try:
        assert no_model.status() == NO_MODEL
        assert no_model.detections() == []
    finally:
        no_model.stop()


def test_loop_recovers_from_an_inference_failure_without_fabricating_results():
    detector = CountingDetector(fail=True)
    source = RepeatingSource()
    service, _, _ = make_service(detector=detector, source=source, fps=30)
    try:
        deadline = time.monotonic() + 2
        while detector.calls == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert service.detections() == []
        assert service.status() == UNAVAILABLE
        detector.fail = False
        wait_for_result(service)
        assert service.detections()[0]["label"] == "chair"
        assert source.open_count >= 2
    finally:
        service.stop()


@pytest.mark.parametrize("class_id,label", [(56, "chair"), (0, "person"), (39, "bottle"), (62, "tv")])
def test_background_loop_labels_use_the_fixed_coco_table(class_id, label):
    assert COCO_CLASSES[class_id] == label
    service, _, _ = make_service(detector=Detector(session=FakeSession(class_id)))
    try:
        wait_for_result(service)
        result = service.detections()
        assert [item["label"] for item in result] == [label]
        assert 0 <= min(result[0]["box"]) and max(result[0]["box"]) <= 1
        assert "age_s" in result[0] and result[0]["simulated"] is False
    finally:
        service.stop()


def gateway_for(live):
    cfg = RobotConfig(api_key="k", watchdog_ms=500, mode=RobotMode.SIMULATOR)
    return CommandGateway(RobotSimulator(cfg), mission_check=lambda: True, live_detections=live)


def test_api_gateway_and_websocket_share_the_same_loop_results(monkeypatch):
    import backend.main as main

    service, detector, _ = make_service()
    monkeypatch.setattr(main, "detection_service", service)
    try:
        wait_for_result(service)
        calls_before_reads = detector.calls
        api_response = asyncio.run(main.get_live_detections())
        gateway = gateway_for(LiveDetections(lambda: service))
        llm_results = run(gateway.detections())

        sent = []
        monkeypatch.setattr(main.ws_manager, "active_connections", {object()})
        async def capture(payload):
            sent.append(payload)
        monkeypatch.setattr(main.ws_manager, "broadcast", capture)
        monkeypatch.setattr(main, "get_robot", lambda: type("Robot", (), {"get_status": lambda self: {"connected": True}})())
        monkeypatch.setattr(main.mission_service, "get_status", lambda: {})
        sleeps = 0
        async def sleep_once(_delay):
            nonlocal sleeps
            sleeps += 1
            if sleeps > 1:
                raise asyncio.CancelledError
        monkeypatch.setattr(main.asyncio, "sleep", sleep_once)
        run(main.telemetry_broadcaster())

        assert api_response["detections"] == llm_results == sent[0]["detections"]
        assert detector.calls == calls_before_reads
        assert api_response["status"] == AVAILABLE
    finally:
        service.stop()


def test_llm_look_and_tell_me_what_you_see_uses_the_live_result_through_the_real_gateway():
    service, detector, _ = make_service(detector=CountingDetector(labels=("chair", "person")))
    try:
        wait_for_result(service)
        calls_before_llm = detector.calls
        adapter = CommandGatewayAdapter(gateway_for(LiveDetections(lambda: service)))

        async def go():
            runner = TaskRunner(adapter, MockBrain(), LoopLimits())
            await runner.start("look and tell me what you see")
            await runner.wait()
            return runner.task

        task = run(go())
        assert task.state == "done" and "chair" in task.answer and "person" in task.answer
        result = next(step.result for step in task.steps if step.tool == "get_detections")
        assert [item["label"] for item in result] == ["chair", "person"]
        assert all("age_s" in item for item in result)
        assert detector.calls == calls_before_llm
    finally:
        service.stop()


def test_a_detector_that_raises_never_breaks_gateway_detections():
    service, detector, _ = make_service(detector=CountingDetector(fail=True), fps=30)
    adapter = CommandGatewayAdapter(gateway_for(LiveDetections(lambda: service)))
    try:
        deadline = time.monotonic() + 2
        while detector.calls == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert detector.calls > 0

        detections = run(adapter.detections())
        status = run(adapter.status())

        assert detections == []
        assert status["detector"] == UNAVAILABLE
    finally:
        service.stop()


def test_simulated_demo_alert_never_enters_the_detection_loop():
    service, _, _ = make_service(camera=Camera(unavailable=True))
    try:
        synthetic_alert = service.generate_simulated_detection()
        assert synthetic_alert.simulated is True
        assert service.detections() == []
    finally:
        service.stop()


def test_prompt_and_tool_description_tell_the_model_what_detections_are():
    from backend.ai.control.brain import SYSTEM_PROMPT
    from backend.ai.control.tools import TOOL_DEFS

    desc = next(tool["description"] for tool in TOOL_DEFS if tool["name"] == "get_detections")
    assert "robot camera" in desc and "age_s" in desc and "empty" in desc
    assert "robot's own camera" in SYSTEM_PROMPT and "age_s" in SYSTEM_PROMPT
    assert "never invent" in SYSTEM_PROMPT.lower() and "empty" in SYSTEM_PROMPT
