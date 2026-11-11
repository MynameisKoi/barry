import time

import numpy as np
import pytest

from backend.ai.vision.detector import Detector, non_max_suppression
from backend.ai.vision.loop import DetectionLoop
from backend.camera.config import CameraConfig
from backend.camera.synthetic import SyntheticSource


def test_nms_suppresses_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 9, 9], [20, 20, 30, 30]], dtype=float)
    assert non_max_suppression(boxes, np.array([.9, .8, .7])) == [0, 2]


class Input:
    name = "images"


class FakeSession:
    def __init__(self, output): self.output = output
    def get_inputs(self): return [Input()]
    def run(self, *_): return [self.output]


def test_detector_parses_yolo11_output_and_letterbox_coordinates():
    output = np.zeros((1, 84, 2), dtype=np.float32)
    output[0, :4, 0] = [320, 320, 160, 160]
    output[0, 4, 0] = .9
    detector = Detector(session=FakeSession(output))
    hits = detector.detect(np.zeros((320, 640, 3), dtype=np.uint8))
    assert len(hits) == 1
    assert hits[0].object == "person" and hits[0].confidence == pytest.approx(.9)
    assert hits[0].bounding_box == pytest.approx([.25, .375, .75, .625])


def test_no_model_never_returns_detections():
    assert Detector("/missing/model.onnx").detect(np.zeros((32, 32, 3), dtype=np.uint8)) == []


class EmptyDetector:
    available = True
    def detect(self, _): return []


def test_detection_loop_with_synthetic_source_and_stale_results():
    loop = DetectionLoop(SyntheticSource(CameraConfig(device="synthetic", width=160, height=120)), EmptyDetector(), fps=30, stale_s=.03)
    loop.start()
    deadline = time.monotonic() + 1
    while loop._updated_at == 0 and time.monotonic() < deadline: time.sleep(.005)
    loop._updated_at = time.monotonic() - 3
    assert not loop.available and loop.latest() == []
    loop.stop()


@pytest.mark.skipif(not __import__("pathlib").Path("models/yolo11n.onnx").exists(), reason="real model not exported")
def test_real_yolo_model_loads():
    assert Detector("models/yolo11n.onnx").available
