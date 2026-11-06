"""Unit tests for the camera service and MJPEG adapter."""

from __future__ import annotations

from dataclasses import replace
import threading
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
    open_service = CameraService(open_source, _config())
    try:
        with pytest.raises(CameraUnavailableError, match="reconnect scheduled"):
            open_service.start()
        assert open_source.open_calls == 3
    finally:
        open_service.stop()

    read_source = FakeSource(read_failures=99)
    read_service = CameraService(read_source, _config())
    try:
        with pytest.raises(CameraUnavailableError, match="reconnect scheduled"):
            read_service.start()
        assert read_source.read_calls == 3
    finally:
        read_service.stop()


def test_capture_failure_stays_down_while_source_keeps_failing() -> None:
    source = FakeSource()
    service = CameraService(source, _config())
    service.start()
    source.read_failures = 99
    deadline = time.monotonic() + 1
    while service.status().capturing and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert not service.status().connected
        assert source.read_calls >= 4  # validated frame plus bounded loop attempts
    finally:
        service.stop()


def test_failed_initial_start_reconnects_in_background() -> None:
    source = FakeSource(read_failures=5)
    service = CameraService(source, _config())
    try:
        with pytest.raises(CameraUnavailableError, match="reconnect scheduled"):
            service.start()
        assert not service.status().connected

        deadline = time.monotonic() + 2
        while not service.status().connected and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.status().connected
        assert service.snapshot().startswith(b"\xff\xd8")
        assert source.open_calls >= 2
    finally:
        service.stop()


def test_runtime_failure_reconnects_and_stream_ends_cleanly() -> None:
    source = FakeSource()
    service = CameraService(source, _config())
    service.start()
    stream = service.stream()
    next(stream)
    source.read_failures = source.read_calls + 3
    try:
        deadline = time.monotonic() + 1
        while service.status().connected and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not service.status().connected
        with pytest.raises(CameraUnavailableError, match="stalled or failed"):
            next(stream)

        deadline = time.monotonic() + 2
        while not service.status().connected and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.status().connected
        assert service.status().active_viewers == 0
    finally:
        stream.close()
        service.stop()


def test_stop_cancels_reconnect_wait_promptly() -> None:
    source = FakeSource(open_failures=99)
    service = CameraService(source, _config())
    with pytest.raises(CameraUnavailableError):
        service.start()
    before = time.monotonic()
    service.stop()
    assert time.monotonic() - before < 0.25
    open_calls = source.open_calls
    time.sleep(0.55)
    assert source.open_calls == open_calls


def test_frame_timing_is_debug_logged_and_rate_limited(caplog) -> None:
    service = CameraService(FakeSource(), _config())
    with caplog.at_level("DEBUG", logger="backend.camera.service"):
        service.start()
        try:
            time.sleep(0.12)
        finally:
            service.stop()
    timing_records = [r for r in caplog.records if "camera frame timing" in r.message]
    assert len(timing_records) == 1
    assert "read_ms=" in timing_records[0].message
    assert "encode_ms=" in timing_records[0].message
    assert "frame_gap_ms=" in timing_records[0].message


class BlockingSource(FrameSource):
    def __init__(self) -> None:
        self.read_calls = 0
        self.close_calls = 0
        self.block_call: int | None = None
        self.read_blocked = threading.Event()
        self.release_read = threading.Event()

    @property
    def name(self) -> str:
        return "blocking"

    def open(self) -> None:
        pass

    def read(self) -> np.ndarray:
        self.read_calls += 1
        call = self.read_calls
        if call == self.block_call:
            self.read_blocked.set()
            self.release_read.wait()
        return np.full((12, 16, 3), call % 255, dtype=np.uint8)

    def close(self) -> None:
        self.close_calls += 1


def test_hung_read_marks_down_and_releases_waiting_viewer() -> None:
    source = BlockingSource()
    source.block_call = 2
    service = CameraService(source, _config(stall_timeout_s=0.1))
    service.start()
    stream = service.stream()
    next(stream)
    try:
        assert source.read_blocked.wait(0.5)
        deadline = time.monotonic() + 0.5
        while service.status().connected and time.monotonic() < deadline:
            time.sleep(0.01)

        status = service.status()
        assert not status.connected
        assert not status.capturing
        with pytest.raises(CameraUnavailableError):
            service.snapshot()
        with pytest.raises(CameraUnavailableError, match="stalled or failed"):
            next(stream)
        assert service.status().active_viewers == 0
    finally:
        source.release_read.set()
        stream.close()
        service.stop()


def test_stop_start_ignores_old_blocked_read_and_old_finally() -> None:
    source = BlockingSource()
    source.block_call = 2
    service = CameraService(source, _config(stall_timeout_s=0.5))
    service.start()
    assert source.read_blocked.wait(0.5)

    before = time.monotonic()
    service.stop()
    assert time.monotonic() - before < 0.25
    closes_after_stop = source.close_calls

    service.start()
    try:
        restarted_frame = service.latest_frame()
        assert restarted_frame.sequence >= 2
        source.release_read.set()
        time.sleep(0.1)
        assert service.status().connected
        assert service.status().capturing
        # The abandoned generation's finally block must not close the new one.
        assert source.close_calls == closes_after_stop
    finally:
        source.release_read.set()
        service.stop()


def test_stall_timeout_is_configurable_from_environment() -> None:
    config = CameraConfig.from_env({"CAMERA_STALL_TIMEOUT_S": "0.25"})
    assert config.stall_timeout_s == 0.25


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
