"""Threaded camera capture service.

The service is the sole owner of a :class:`FrameSource`.  It converts raw BGR
frames to JPEG once per capture and publishes only the newest encoded frame;
viewers therefore never create queues or slow the capture loop.
"""

from __future__ import annotations

from collections import deque
import threading
import time
from typing import Deque, Iterator, Optional

import cv2

from .config import CameraConfig
from .interface import (
    CameraInterface,
    CameraUnavailableError,
    Frame,
    FrameCaptureError,
    ViewerLimitError,
)
from .source import FrameSource
from .status import CameraStatus, Resolution


_MAX_ATTEMPTS = 3
_INITIAL_BACKOFF_SECONDS = 0.05
_FPS_WINDOW_SIZE = 60


class CameraService(CameraInterface):
    """Capture raw frames on one thread and serve the latest JPEG safely."""

    def __init__(self, source: FrameSource, config: CameraConfig) -> None:
        self._source = source
        self._config = config
        self._condition = threading.Condition(threading.RLock())
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest: Optional[Frame] = None
        self._sequence = 0
        self._active_viewers = 0
        self._connected = False
        self._capturing = False
        self._capture_times: Deque[float] = deque(maxlen=_FPS_WINDOW_SIZE)

    def start(self) -> None:
        """Open and validate the source, then launch the capture thread."""
        with self._condition:
            if self._capturing:
                return
            if not self._config.enabled:
                raise CameraUnavailableError("camera is disabled")

            self._stop_event.clear()
            self._capture_times.clear()
            try:
                self._open_with_retries()
                raw = self._read_with_retries()
                self._publish(raw)
            except Exception as exc:
                self._connected = False
                self._capturing = False
                self._safe_close_source()
                if isinstance(exc, CameraUnavailableError):
                    raise
                raise CameraUnavailableError(
                    f"camera source {self._source.name!r} failed to start"
                ) from exc

            self._connected = True
            self._capturing = True
            self._thread = threading.Thread(
                target=self._capture_loop,
                name=f"camera-capture-{self._source.name}",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop capture and release the source; safe to call repeatedly."""
        with self._condition:
            self._stop_event.set()
            self._capturing = False
            self._condition.notify_all()
            thread = self._thread

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, 3.0 / self._config.target_fps))

        self._safe_close_source()
        with self._condition:
            self._connected = False
            self._thread = None
            self._condition.notify_all()

    def snapshot(self) -> bytes:
        return self.latest_frame().jpeg

    def latest_frame(self) -> Frame:
        with self._condition:
            frame = self._latest
            if not self._connected or frame is None:
                raise CameraUnavailableError("no camera frame is available")
            if time.monotonic() - frame.timestamp > self._stale_after_seconds:
                raise CameraUnavailableError("latest camera frame is stale")
            return frame

    def stream(self) -> Iterator[Frame]:
        """Yield sequence changes and unregister the viewer on every exit path."""
        registered = False
        try:
            with self._condition:
                if self._active_viewers >= self._config.max_viewers:
                    raise ViewerLimitError("camera viewer limit reached")
                if not self._connected or not self._capturing:
                    raise CameraUnavailableError("camera is not capturing")
                self._active_viewers += 1
                registered = True
                last_sequence = -1

            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: (
                            self._latest is not None
                            and self._latest.sequence != last_sequence
                        )
                        or not self._capturing
                    )
                    if not self._capturing:
                        return
                    frame = self._latest
                    if frame is None:
                        continue
                    last_sequence = frame.sequence
                yield frame
        finally:
            if registered:
                with self._condition:
                    self._active_viewers -= 1
                    self._condition.notify_all()

    def status(self) -> CameraStatus:
        with self._condition:
            frame = self._latest
            now = time.monotonic()
            resolution = (
                Resolution(width=frame.width, height=frame.height) if frame else None
            )
            age_ms = max(0.0, (now - frame.timestamp) * 1000.0) if frame else None
            actual_fps = None
            if len(self._capture_times) >= 2:
                elapsed = self._capture_times[-1] - self._capture_times[0]
                if elapsed > 0:
                    actual_fps = (len(self._capture_times) - 1) / elapsed
            return CameraStatus(
                connected=self._connected,
                capturing=self._capturing,
                streaming=self._active_viewers > 0,
                resolution=resolution,
                target_fps=self._config.target_fps,
                actual_fps=actual_fps,
                active_viewers=self._active_viewers,
                last_frame_age_ms=age_ms,
            )

    @property
    def _stale_after_seconds(self) -> float:
        # Allow several missed target intervals, with a practical lower bound.
        return max(1.0, 3.0 / self._config.target_fps)

    def _open_with_retries(self) -> None:
        last_error: Optional[BaseException] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                self._source.open()
                return
            except Exception as exc:
                last_error = exc
                self._safe_close_source()
                if attempt + 1 < _MAX_ATTEMPTS:
                    self._stop_event.wait(_INITIAL_BACKOFF_SECONDS * (2**attempt))
        raise CameraUnavailableError(
            f"unable to open camera source {self._source.name!r}"
        ) from last_error

    def _read_with_retries(self):
        last_error: Optional[BaseException] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return self._source.read()
            except Exception as exc:
                last_error = exc
                if attempt + 1 < _MAX_ATTEMPTS:
                    self._stop_event.wait(_INITIAL_BACKOFF_SECONDS * (2**attempt))
        raise FrameCaptureError("camera frame capture failed") from last_error

    def _encode(self, raw) -> tuple[bytes, int, int]:
        if raw is None or getattr(raw, "ndim", 0) < 2:
            raise FrameCaptureError("camera source returned an invalid frame")
        height, width = raw.shape[:2]
        try:
            ok, encoded = cv2.imencode(
                ".jpg",
                raw,
                [int(cv2.IMWRITE_JPEG_QUALITY), self._config.jpeg_quality],
            )
        except Exception as exc:
            raise FrameCaptureError("JPEG encoding failed") from exc
        if not ok or encoded is None:
            raise FrameCaptureError("JPEG encoding failed")
        return encoded.tobytes(), width, height

    def _publish(self, raw) -> None:
        jpeg, width, height = self._encode(raw)
        captured_at = time.monotonic()
        with self._condition:
            self._sequence += 1
            self._latest = Frame(
                jpeg=jpeg,
                timestamp=captured_at,
                width=width,
                height=height,
                sequence=self._sequence,
            )
            self._capture_times.append(captured_at)
            self._condition.notify_all()

    def _capture_loop(self) -> None:
        interval = 1.0 / self._config.target_fps
        deadline = time.monotonic() + interval
        try:
            while not self._stop_event.is_set():
                delay = deadline - time.monotonic()
                if delay > 0 and self._stop_event.wait(delay):
                    return
                try:
                    self._publish(self._read_with_retries())
                except Exception:
                    with self._condition:
                        self._connected = False
                        self._capturing = False
                        self._condition.notify_all()
                    return
                deadline += interval
                if deadline < time.monotonic():
                    deadline = time.monotonic() + interval
        finally:
            self._safe_close_source()
            with self._condition:
                self._connected = False
                self._capturing = False
                self._condition.notify_all()

    def _safe_close_source(self) -> None:
        try:
            self._source.close()
        except Exception:
            # CameraInterface.stop() is explicitly non-raising.
            pass

