import time

import numpy as np
import pytest

from backend.ai.vision.detector import COCO_CLASSES, Detector, non_max_suppression
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


def test_coco_class_names_keep_multiword_labels_and_all_80_ids():
    assert len(COCO_CLASSES) == 80
    assert COCO_CLASSES[9] == "traffic light"
    assert COCO_CLASSES[56] == "chair"
    assert COCO_CLASSES[79] == "toothbrush"


def test_detector_maps_synthetic_top_class_56_to_chair():
    output = np.zeros((1, 84, 1), dtype=np.float32)
    output[0, :4, 0] = [320, 320, 160, 160]
    output[0, 4 + 56, 0] = .95
    hits = Detector(session=FakeSession(output)).detect(np.zeros((640, 640, 3), dtype=np.uint8))
    assert len(hits) == 1
    assert hits[0].object == "chair"


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


class FailsFirstReadSource:
    name = "fails-once"
    def __init__(self): self.reads = 0; self.opens = 0; self.opened = False
    def open(self): self.opens += 1; self.opened = True
    def close(self): self.opened = False
    def read(self):
        self.reads += 1
        if self.reads == 1: raise RuntimeError("transient camera hiccup")
        return np.zeros((32, 32, 3), dtype=np.uint8)


class OneDetection:
    available = True
    def detect(self, _):
        from backend.models import DetectionAlert
        return [DetectionAlert(object="person", confidence=.9, zone="camera", timestamp="now")]


def test_detection_loop_recovers_after_transient_source_error():
    source = FailsFirstReadSource()
    loop = DetectionLoop(source, OneDetection(), fps=30)
    loop.start()
    deadline = time.monotonic() + 3
    while not loop.latest() and time.monotonic() < deadline: time.sleep(.01)
    assert loop.latest()[0].object == "person"
    assert source.opens >= 2
    loop.stop()


@pytest.mark.skipif(not __import__("pathlib").Path("models/yolo11n.onnx").exists(), reason="real model not exported")
def test_real_yolo_model_loads():
    assert Detector("models/yolo11n.onnx").available
