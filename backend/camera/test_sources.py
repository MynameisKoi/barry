"""Tests for raw physical and synthetic frame sources."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from .config import CameraConfig
from .interface import CameraUnavailableError, FrameCaptureError
from .test_source import SyntheticSource
from .yahboom import YahboomSource


class FakeCapture:
    def __init__(self, reads, opened: bool = True) -> None:
        self._reads = iter(reads)
        self._opened = opened
        self.released = 0
        self.settings = []

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        return next(self._reads)

    def set(self, prop_id: int, value: float) -> bool:
        self.settings.append((prop_id, value))
        return True

    def release(self) -> None:
        self.released += 1


def test_yahboom_configures_device_and_preserves_validation_frame() -> None:
    first = np.full((3, 4, 3), 7, dtype=np.uint8)
    second = np.full((3, 4, 3), 9, dtype=np.uint8)
    capture = FakeCapture([(True, first), (True, second)])
    devices = []
    config = CameraConfig(device="/dev/video2", width=800, height=600, target_fps=15)
    source = YahboomSource(config, lambda device: devices.append(device) or capture)

    source.open()

    assert devices == ["/dev/video2"]
    assert capture.settings == [
        (cv2.CAP_PROP_FRAME_WIDTH, 800.0),
        (cv2.CAP_PROP_FRAME_HEIGHT, 600.0),
        (cv2.CAP_PROP_FPS, 15.0),
    ]
    assert source.name == "yahboom"
    assert source.read() is first
    assert source.read() is second


@pytest.mark.parametrize(
    "capture",
    [
        FakeCapture([], opened=False),
        FakeCapture([(False, None)]),
        FakeCapture([(True, np.empty((0, 0, 3), dtype=np.uint8))]),
        FakeCapture([(True, np.zeros((2, 2), dtype=np.uint8))]),
    ],
)
def test_yahboom_open_rejects_unavailable_or_invalid_camera(capture) -> None:
    source = YahboomSource(CameraConfig(), lambda _device: capture)

    with pytest.raises(CameraUnavailableError):
        source.open()

    assert capture.released == 1


def test_yahboom_translates_capture_failure_and_closes_idempotently() -> None:
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    capture = FakeCapture([(True, frame), (False, None)])
    source = YahboomSource(CameraConfig(), lambda _device: capture)
    source.open()
    source.read()

    with pytest.raises(FrameCaptureError):
        source.read()

    source.close()
    source.close()
    assert capture.released == 1
    with pytest.raises(FrameCaptureError):
        source.read()


def test_synthetic_source_generates_animated_bgr_frames() -> None:
    source = SyntheticSource(
        CameraConfig(device="synthetic", width=320, height=240)
    )
    source.open()

    first = source.read()
    second = source.read()

    assert source.name == "synthetic"
    assert first.shape == (240, 320, 3)
    assert first.dtype == np.uint8
    assert not np.array_equal(first, second)
    source.close()
    source.close()
    with pytest.raises(FrameCaptureError):
        source.read()


def test_synthetic_source_requires_synthetic_device() -> None:
    source = SyntheticSource(CameraConfig(device=0))

    with pytest.raises(CameraUnavailableError):
        source.open()
