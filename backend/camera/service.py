"""Threaded camera capture service.

The service is the sole owner of a :class:`FrameSource`.  It converts raw BGR
frames to JPEG once per capture and publishes only the newest encoded frame;
viewers therefore never create queues or slow the capture loop.
"""

from __future__ import annotations

from collections import deque
import logging
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
_RECONNECT_INITIAL_SECONDS = 0.5
_RECONNECT_MAX_SECONDS = 5.0
_FPS_WINDOW_SIZE = 60
_TIMING_LOG_INTERVAL_SECONDS = 5.0

logger = logging.getLogger(__name__)


class _FrameReadTimeout(FrameCaptureError):
    """Internal signal that a source read exceeded the watchdog deadline."""


class CameraService(CameraInterface):
    """Capture raw frames on one thread and serve the latest JPEG safely."""

    def __init__(self, source: FrameSource, config: CameraConfig) -> None:
        self._source = source
        self._config = config
        self._condition = threading.Condition(threading.RLock())
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._generation = 0
        self._latest: Optional[Frame] = None
        self._sequence = 0
        self._active_viewers = 0
        self._connected = False
        self._capturing = False
        self._stream_error: Optional[CameraUnavailableError] = None
        self._capture_times: Deque[float] = deque(maxlen=_FPS_WINDOW_SIZE)
        self._last_timing_log = float("-inf")

    def start(self) -> None:
        """Open and validate the source, then launch the capture thread."""
        with self._condition:
            if (
                self._thread is not None
                and self._thread.is_alive()
                and not self._stop_event.is_set()
            ):
                return
            if not self._config.enabled:
                raise CameraUnavailableError("camera is disabled")

            self._generation += 1
            generation = self._generation
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._thread = None
            self._connected = False
            self._capturing = False
            self._stream_error = None
            self._capture_times.clear()
            self._condition.notify_all()

        initial_error: Optional[BaseException] = None
        try:
            self._connect(generation, stop_event)
        except Exception as exc:
            initial_error = exc
            self._mark_disconnected(
                generation, CameraUnavailableError("camera startup failed")
            )

        with self._condition:
            if not self._owns_generation(generation) or stop_event.is_set():
                raise CameraUnavailableError("camera start was cancelled") from initial_error
            self._thread = threading.Thread(
                target=self._capture_loop,
                args=(generation, stop_event),
                name=f"camera-capture-{self._source.name}",
                daemon=True,
            )
            self._thread.start()

            if initial_error is not None:
                raise CameraUnavailableError(
                    f"camera source {self._source.name!r} failed to start; "
                    "reconnect scheduled"
                ) from initial_error

    def stop(self) -> None:
        """Stop capture and release the source; safe to call repeatedly."""
        with self._condition:
            stop_event = self._stop_event
            stop_event.set()
            self._generation += 1
            self._capturing = False
            self._connected = False
            self._stream_error = None
            self._condition.notify_all()
            thread = self._thread
            self._thread = None
            self._safe_close_source()

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, 3.0 / self._config.target_fps))

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
                        if self._stream_error is not None:
                            raise CameraUnavailableError(str(self._stream_error))
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

    def _owns_generation(self, generation: int) -> bool:
        return generation == self._generation

    def _open_with_retries(
        self, generation: int, stop_event: threading.Event
    ) -> None:
        last_error: Optional[BaseException] = None
        for attempt in range(_MAX_ATTEMPTS):
            if stop_event.is_set() or not self._owns_generation(generation):
                raise CameraUnavailableError("camera service is stopping")
            try:
                self._source.open()
                return
            except Exception as exc:
                last_error = exc
                self._safe_close_source(generation)
                if attempt + 1 < _MAX_ATTEMPTS:
                    if stop_event.wait(
                        _INITIAL_BACKOFF_SECONDS * (2**attempt)
                    ):
                        raise CameraUnavailableError(
                            "camera service is stopping"
                        ) from exc
        raise CameraUnavailableError(
            f"unable to open camera source {self._source.name!r}"
        ) from last_error

    def _read_once_with_timeout(
        self, generation: int, stop_event: threading.Event
    ):
        completed = threading.Event()
        result = []

        def read_source() -> None:
            try:
                result.append((True, self._source.read()))
            except BaseException as exc:
                result.append((False, exc))
            finally:
                completed.set()

        threading.Thread(
            target=read_source,
            name=f"camera-read-{self._source.name}-g{generation}",
            daemon=True,
        ).start()
        deadline = time.monotonic() + self._config.stall_timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _FrameReadTimeout(
                    f"camera frame read stalled for {self._config.stall_timeout_s:.2f}s"
                )
            if completed.wait(min(remaining, 0.05)):
                succeeded, value = result[0]
                if stop_event.is_set() or not self._owns_generation(generation):
                    raise FrameCaptureError("camera session ended during frame read")
                if succeeded:
                    return value
                raise value
            if stop_event.is_set() or not self._owns_generation(generation):
                raise FrameCaptureError("camera session ended during frame read")

    def _read_with_retries(
        self, generation: int, stop_event: threading.Event
    ):
        last_error: Optional[BaseException] = None
        for attempt in range(_MAX_ATTEMPTS):
            if stop_event.is_set() or not self._owns_generation(generation):
                raise FrameCaptureError("camera service is stopping")
            try:
                return self._read_once_with_timeout(generation, stop_event)
            except _FrameReadTimeout:
                # Retrying a read on the same stalled session can create an
                # unbounded pile of blocked workers. Reconnect immediately.
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < _MAX_ATTEMPTS:
                    if stop_event.wait(
                        _INITIAL_BACKOFF_SECONDS * (2**attempt)
                    ):
                        raise FrameCaptureError(
                            "camera service is stopping"
                        ) from exc
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

    def _publish(
        self, raw, generation: int
    ) -> tuple[float, Optional[float]]:
        encode_started = time.monotonic()
        jpeg, width, height = self._encode(raw)
        captured_at = time.monotonic()
        encode_seconds = captured_at - encode_started
        with self._condition:
            if not self._owns_generation(generation):
                raise FrameCaptureError("discarding frame from stale camera session")
            previous_at = (
                self._latest.timestamp if self._latest is not None else None
            )
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
        frame_gap_seconds = (
            captured_at - previous_at if previous_at is not None else None
        )
        return encode_seconds, frame_gap_seconds

    def _capture_and_publish(
        self, generation: int, stop_event: threading.Event
    ) -> None:
        read_started = time.monotonic()
        raw = self._read_with_retries(generation, stop_event)
        read_finished = time.monotonic()
        encode_seconds, frame_gap_seconds = self._publish(raw, generation)
        now = time.monotonic()
        if logger.isEnabledFor(logging.DEBUG) and (
            now - self._last_timing_log >= _TIMING_LOG_INTERVAL_SECONDS
        ):
            frame_gap_ms = (
                frame_gap_seconds * 1000.0
                if frame_gap_seconds is not None
                else None
            )
            logger.debug(
                "camera frame timing source=%s read_ms=%.2f encode_ms=%.2f "
                "frame_gap_ms=%s",
                self._source.name,
                (read_finished - read_started) * 1000.0,
                encode_seconds * 1000.0,
                f"{frame_gap_ms:.2f}" if frame_gap_ms is not None else "unknown",
            )
            self._last_timing_log = now

    def _connect(self, generation: int, stop_event: threading.Event) -> None:
        self._open_with_retries(generation, stop_event)
        try:
            with self._condition:
                if not self._owns_generation(generation):
                    raise CameraUnavailableError("camera session is stale")
                self._capture_times.clear()
            self._capture_and_publish(generation, stop_event)
        except Exception:
            self._safe_close_source(generation)
            raise
        with self._condition:
            if not self._owns_generation(generation):
                raise CameraUnavailableError("camera session is stale")
            self._connected = True
            self._capturing = True
            self._stream_error = None
            self._condition.notify_all()

    def _mark_disconnected(
        self,
        generation: int,
        stream_error: Optional[CameraUnavailableError] = None,
    ) -> None:
        with self._condition:
            if not self._owns_generation(generation):
                return
            self._safe_close_source()
            self._connected = False
            self._capturing = False
            if stream_error is not None:
                self._stream_error = stream_error
            self._condition.notify_all()

    def _capture_loop(
        self, generation: int, stop_event: threading.Event
    ) -> None:
        interval = 1.0 / self._config.target_fps
        deadline = time.monotonic() + interval
        reconnect_delay = _RECONNECT_INITIAL_SECONDS
        try:
            while not stop_event.is_set() and self._owns_generation(generation):
                with self._condition:
                    connected = self._connected
                if not connected:
                    if stop_event.wait(reconnect_delay):
                        return
                    try:
                        self._connect(generation, stop_event)
                    except Exception as exc:
                        logger.warning(
                            "camera reconnect failed source=%s retry_in=%.1fs: %s",
                            self._source.name,
                            min(reconnect_delay * 2, _RECONNECT_MAX_SECONDS),
                            exc,
                        )
                        self._mark_disconnected(
                            generation,
                            CameraUnavailableError("camera reconnect failed"),
                        )
                        reconnect_delay = min(
                            reconnect_delay * 2, _RECONNECT_MAX_SECONDS
                        )
                        continue
                    logger.info("camera reconnected source=%s", self._source.name)
                    reconnect_delay = _RECONNECT_INITIAL_SECONDS
                    deadline = time.monotonic() + interval

                delay = deadline - time.monotonic()
                if delay > 0 and stop_event.wait(delay):
                    return
                try:
                    self._capture_and_publish(generation, stop_event)
                except Exception as exc:
                    logger.warning(
                        "camera capture failed source=%s; reconnecting: %s",
                        self._source.name,
                        exc,
                    )
                    self._mark_disconnected(
                        generation,
                        CameraUnavailableError("camera capture stalled or failed"),
                    )
                    reconnect_delay = _RECONNECT_INITIAL_SECONDS
                    continue
                deadline += interval
                if deadline < time.monotonic():
                    deadline = time.monotonic() + interval
        finally:
            self._mark_disconnected(generation)

    def _safe_close_source(self, generation: Optional[int] = None) -> None:
        if generation is not None and not self._owns_generation(generation):
            return
        try:
            self._source.close()
        except Exception:
            # CameraInterface.stop() is explicitly non-raising.
            pass

