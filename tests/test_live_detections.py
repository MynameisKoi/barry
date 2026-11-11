"""Live YOLO detections through CommandGateway.detections(): on demand, cached 1 s, age_s, never fabricated."""

import asyncio
import threading

import cv2
import numpy as np
import pytest

from backend.ai.control.executor import LoopLimits, TaskRunner
from backend.ai.control.brain import MockBrain
from backend.ai.control.gateway_adapter import CommandGatewayAdapter
from backend.ai.detection import AIDetectionService
from backend.ai.vision.detector import COCO_CLASSES, Detector
from backend.camera.interface import CameraUnavailableError, Frame
from backend.gateway import CommandGateway
from backend.gateway.detections import AVAILABLE, NO_CAMERA, NO_MODEL, UNAVAILABLE, LiveDetections
from backend.models import DetectionAlert
from backend.robot.config import RobotConfig
from backend.robot.interface import RobotMode
from backend.robot.simulator import RobotSimulator


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def jpeg(color=(30, 60, 90)) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((48, 64, 3), color, dtype=np.uint8))
    assert ok
    return buf.tobytes()


class FakeCamera:
    def __init__(self, clock, frame_age=0.0, raises=None):
        self.clock, self.frame_age, self.raises = clock, frame_age, raises

    def latest_frame(self):
        if self.raises:
            raise self.raises
        return Frame(jpeg=jpeg(), timestamp=self.clock() - self.frame_age, width=64, height=48, sequence=1)


class FakeDetector:
    """Stands in for backend.ai.vision.Detector: counts inference calls and records the thread."""

    def __init__(self, labels=("chair",), available=True, fail=False):
        self.labels, self.available, self.fail = labels, available, fail
        self.calls = 0
        self.threads = []
        self.shapes = []

    def detect(self, frame, zone="Robot camera"):
        self.calls += 1
        self.threads.append(threading.current_thread())
        self.shapes.append(frame.shape)
        if self.fail:
            raise RuntimeError("onnx exploded")
        return [DetectionAlert(object=label, confidence=0.876, zone=zone, timestamp="t",
                               bounding_box=[0.1, 0.2, 0.5, 0.6]) for label in self.labels]


def make(detector=None, camera=..., clock=None, **kw):
    clock = clock or Clock()
    detector = FakeDetector() if detector is None else detector
    cam = FakeCamera(clock) if camera is ... else camera
    return LiveDetections(lambda: cam, detector, clock=clock, cache_s=kw.pop("cache_s", 1.0),
                          max_age_s=kw.pop("max_age_s", 2.0)), detector, clock


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ fresh, cache, age

def test_fresh_call_runs_inference_once_and_returns_label_confidence_box_and_age():
    live, det, _ = make()
    result = run(live.detections())
    assert det.calls == 1
    assert result == [{"label": "chair", "confidence": 0.88, "box": [0.1, 0.2, 0.5, 0.6], "age_s": 0.0}]
    assert det.shapes[0] == (48, 64, 3)            # the camera's JPEG was decoded to an image


def test_repeated_calls_within_one_second_reuse_the_cache_and_age_grows():
    live, det, clock = make()

    async def go():
        first = await live.detections()
        clock.now += 0.6
        second = await live.detections()
        clock.now += 0.3
        third = await live.detections()
        return first, second, third

    first, second, third = run(go())
    assert det.calls == 1
    assert [r[0]["age_s"] for r in (first, second, third)] == [0.0, 0.6, 0.9]
    assert second[0]["label"] == "chair"


def test_after_the_cache_window_inference_runs_again_on_the_newest_frame():
    live, det, clock = make()

    async def go():
        await live.detections()
        clock.now += 1.2
        return await live.detections()

    result = run(go())
    assert det.calls == 2
    assert result[0]["age_s"] == 0.0               # a fresh frame again


def test_concurrent_calls_share_one_inference():
    live, det, _ = make()

    async def go():
        return await asyncio.gather(*(live.detections() for _ in range(5)))

    results = run(go())
    assert det.calls == 1 and all(r[0]["label"] == "chair" for r in results)


def test_inference_runs_in_a_worker_thread_not_the_event_loop():
    live, det, _ = make()

    async def go():
        loop_thread = threading.current_thread()
        await live.detections()
        return loop_thread

    loop_thread = run(go())
    assert det.threads[0] is not loop_thread


# ------------------------------------------------------------------ stale / unavailable / never fabricated

def test_a_stale_frame_returns_nothing_and_runs_no_inference():
    clock = Clock()
    live, det, _ = make(camera=FakeCamera(clock, frame_age=2.5), clock=clock)
    assert run(live.detections()) == []
    assert det.calls == 0
    assert live.state() == UNAVAILABLE


def test_a_cached_result_is_dropped_once_it_would_be_older_than_the_max_age():
    live, det, clock = make(cache_s=10.0, max_age_s=2.0)

    async def go():
        await live.detections()
        clock.now += 2.5                           # cache still "fresh", but the frame is now 2.5 s old
        return await live.detections()

    assert run(go()) == []


def test_no_camera_returns_nothing_with_state_no_camera():
    live, det, _ = make(camera=None)
    assert run(live.detections()) == [] and live.state() == NO_CAMERA and det.calls == 0


def test_camera_without_a_fresh_frame_is_no_camera():
    clock = Clock()
    live, det, _ = make(camera=FakeCamera(clock, raises=CameraUnavailableError("stale")), clock=clock)
    assert run(live.detections()) == [] and live.state() == NO_CAMERA and det.calls == 0


@pytest.mark.parametrize("detector", [None, FakeDetector(available=False)])
def test_no_model_returns_nothing_with_state_no_model(detector):
    clock = Clock()
    live = LiveDetections(lambda: FakeCamera(clock), detector, clock=clock)
    assert run(live.detections()) == [] and live.state() == NO_MODEL


def test_an_inference_error_returns_nothing_marks_unavailable_and_recovers():
    live, det, clock = make(detector=FakeDetector(fail=True))

    async def go():
        first = await live.detections()
        state_after_failure = live.state()
        det.fail = False
        second = await live.detections()          # failures are not cached: the next call retries
        return first, state_after_failure, second

    first, state_after_failure, second = run(go())
    assert first == [] and state_after_failure == UNAVAILABLE
    assert second[0]["label"] == "chair" and live.state() == AVAILABLE


def test_state_is_available_with_model_camera_and_fresh_frame():
    live, _, _ = make()
    assert live.state() == AVAILABLE


def test_corrupt_jpeg_is_a_failure_not_a_detection():
    clock = Clock()

    class Broken(FakeCamera):
        def latest_frame(self):
            return Frame(jpeg=b"not a jpeg", timestamp=clock(), width=1, height=1, sequence=1)

    live, det, _ = make(camera=Broken(clock), clock=clock)
    assert run(live.detections()) == [] and det.calls == 0 and live.state() == UNAVAILABLE


def test_empty_detector_output_is_an_empty_list_not_an_error():
    live, _, _ = make(detector=FakeDetector(labels=()))
    assert run(live.detections()) == [] and live.state() == AVAILABLE


# ------------------------------------------------------------------ labels come from the fixed COCO table

class FakeSession:
    """An ONNX session stand-in producing one YOLO row for `class_id` (output shape 1 x 84 x 1)."""

    def __init__(self, class_id, score=0.9):
        self.class_id, self.score = class_id, score

    def get_inputs(self):
        return [type("I", (), {"name": "images"})()]

    def run(self, _names, _feed):
        out = np.zeros((1, 84, 1), dtype=np.float32)
        out[0, 0:4, 0] = (320, 320, 200, 200)
        out[0, 4 + self.class_id, 0] = self.score
        return [out]


@pytest.mark.parametrize("class_id, label", [(56, "chair"), (0, "person"), (39, "bottle"), (62, "tv")])
def test_labels_come_from_the_coco_table_through_the_real_detector(class_id, label):
    assert COCO_CLASSES[class_id] == label
    clock = Clock()
    detector = Detector(session=FakeSession(class_id))
    live = LiveDetections(lambda: FakeCamera(clock), detector, clock=clock)
    result = run(live.detections())
    assert [r["label"] for r in result] == [label]
    assert 0 <= min(result[0]["box"]) and max(result[0]["box"]) <= 1 and "age_s" in result[0]
    assert "simulated" not in result[0]


def test_the_simulated_demo_alert_never_reaches_detections():
    live, det, _ = make(camera=None)
    AIDetectionService().generate_simulated_detection()   # exists for the demo endpoint only
    assert run(live.detections()) == [] and det.calls == 0


# ------------------------------------------------------------------ gateway + LLM

@pytest.fixture
def gateway_factory():
    def build(live):
        cfg = RobotConfig(api_key="k", watchdog_ms=500, mode=RobotMode.SIMULATOR)
        return CommandGateway(RobotSimulator(cfg), mission_check=lambda: True, live_detections=live)
    return build


def test_gateway_detections_and_status_come_from_the_live_detector(gateway_factory):
    live, det, _ = make()
    gateway = gateway_factory(live)
    assert run(gateway.status())["detector"] == AVAILABLE
    assert run(gateway.detections())[0]["label"] == "chair" and det.calls == 1


@pytest.mark.parametrize("build, state", [
    (lambda: make(camera=None)[0], NO_CAMERA),
    (lambda: make(detector=FakeDetector(available=False))[0], NO_MODEL),
    (lambda: make(camera=FakeCamera(Clock(), frame_age=9, raises=None), clock=Clock())[0], UNAVAILABLE),
])
def test_gateway_status_reports_the_real_detector_state(gateway_factory, build, state):
    gateway = gateway_factory(build())
    assert run(gateway.status())["detector"] == state
    assert run(gateway.detections()) == []


def test_gateway_without_a_detector_returns_nothing_and_says_unavailable(gateway_factory):
    gateway = gateway_factory(None)
    assert run(gateway.detections()) == [] and run(gateway.status())["detector"] == "unavailable"


def test_a_detector_that_raises_never_breaks_gateway_detections(gateway_factory):
    class Exploding:
        def state(self): return AVAILABLE
        async def detections(self): raise RuntimeError("boom")

    assert run(gateway_factory(Exploding()).detections()) == []


def test_llm_look_and_tell_me_what_you_see_uses_the_live_result_through_the_real_gateway(gateway_factory):
    live, det, _ = make(detector=FakeDetector(labels=("chair", "person")))
    adapter = CommandGatewayAdapter(gateway_factory(live))

    async def go():
        runner = TaskRunner(adapter, MockBrain(), LoopLimits())
        await runner.start("look and tell me what you see")
        await runner.wait()
        return runner.task

    task = run(go())
    assert task.state == "done" and "chair" in task.answer and "person" in task.answer
    got = [s for s in task.steps if s.tool == "get_detections"][0].result
    assert got[0]["label"] == "chair" and "age_s" in got[0]
    assert det.calls == 1                         # the start-of-task context and the tool call share one inference


def test_prompt_and_tool_description_tell_the_model_what_detections_are():
    from backend.ai.control.brain import SYSTEM_PROMPT
    from backend.ai.control.tools import TOOL_DEFS

    desc = next(t["description"] for t in TOOL_DEFS if t["name"] == "get_detections")
    assert "robot camera" in desc and "age_s" in desc and "empty" in desc
    assert "robot's own camera" in SYSTEM_PROMPT and "age_s" in SYSTEM_PROMPT
    assert "never invent" in SYSTEM_PROMPT.lower() and "empty" in SYSTEM_PROMPT
