"""Unit tests for the camera service and MJPEG adapter."""

from __future__ import annotations

from dataclasses import replace
import time

import numpy as np
import pytest

from .config import CameraConfig
from .interface import (
    CameraUnavailableError,
    Frame,
    FrameCaptureError,
    ViewerLimitError,
)
from .service import CameraService
from .source import FrameSource
from .stream import mjpeg_generator


class FakeSource(FrameSource):
    def __init__(self, *, open_failures: int = 0, read_failures: int = 0) -> None:
        self.open_failures = open_failures
        self.read_failures = read_failures
        self.open_calls = 0
        self.read_calls = 0
        self.close_calls = 0

    @property
    def name(self) -> str:
        return "fake"

    def open(self) -> None:
        self.open_calls += 1
        if self.open_calls <= self.open_failures:
            raise CameraUnavailableError("not ready")

    def read(self) -> np.ndarray:
        self.read_calls += 1
        if self.read_calls <= self.read_failures:
            raise FrameCaptureError("temporary failure")
        return np.full((12, 16, 3), self.read_calls % 255, dtype=np.uint8)

    def close(self) -> None:
        self.close_calls += 1


def _config(**changes) -> CameraConfig:
    return CameraConfig(target_fps=30, **changes)


def test_start_snapshot_status_and_idempotent_stop() -> None:
    source = FakeSource()
    service = CameraService(source, _config())
    service.start()
    try:
        jpeg = service.snapshot()
        assert jpeg.startswith(b"\xff\xd8")
        status = service.status()
        assert status.connected and status.capturing
        assert (status.resolution.width, status.resolution.height) == (16, 12)
        assert status.last_frame_age_ms is not None
        deadline = time.monotonic() + 0.5
        while service.status().actual_fps is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.status().actual_fps is not None
    finally:
        service.stop()
        service.stop()
    assert not service.status().connected
    assert source.close_calls >= 2


def test_slow_stream_viewer_skips_intermediate_frames() -> None:
    service = CameraService(FakeSource(), _config())
    service.start()
    stream = service.stream()
    try:
        first = next(stream)
        time.sleep(0.12)
        second = next(stream)
        assert second.sequence > first.sequence + 1
    finally:
        stream.close()
        service.stop()
    assert service.status().active_viewers == 0


def test_viewer_limit_is_enforced_and_released() -> None:
    service = CameraService(FakeSource(), _config(max_viewers=1))
    service.start()
    first = service.stream()
    second = service.stream()
    try:
        next(first)
        assert service.status().streaming
        with pytest.raises(ViewerLimitError):
            next(second)
        first.close()
        assert service.status().active_viewers == 0
        replacement = service.stream()
        next(replacement)
        replacement.close()
    finally:
        first.close()
        second.close()
        service.stop()


def test_snapshot_rejects_stale_frame() -> None:
    service = CameraService(FakeSource(), CameraConfig(target_fps=1))
    service.start()
    try:
        with service._condition:
            service._latest = replace(
                service._latest,
                timestamp=time.monotonic() - service._stale_after_seconds - 1,
            )
        with pytest.raises(CameraUnavailableError, match="stale"):
            service.snapshot()
    finally:
        service.stop()


def test_start_uses_bounded_open_and_read_retries() -> None:
    open_source = FakeSource(open_failures=99)
    with pytest.raises(CameraUnavailableError):
        CameraService(open_source, _config()).start()
    assert open_source.open_calls == 3

    read_source = FakeSource(read_failures=99)
    with pytest.raises(CameraUnavailableError):
        CameraService(read_source, _config()).start()
    assert read_source.read_calls == 3


def test_capture_failure_disconnects_after_bounded_retries() -> None:
    source = FakeSource()
    service = CameraService(source, _config())
    service.start()
    source.read_failures = 99
    deadline = time.monotonic() + 1
    while service.status().capturing and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert not service.status().connected
        assert source.read_calls == 4  # validated frame plus three loop attempts
    finally:
        service.stop()


class StubService:
    def stream(self):
        yield Frame(b"jpeg", 1.0, 4, 3, 7)


def test_mjpeg_generator_includes_content_length() -> None:
    assert next(mjpeg_generator(StubService())) == (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: 4\r\n\r\n"
        b"jpeg\r\n"
    )
