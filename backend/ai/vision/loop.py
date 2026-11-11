"""Single paced YOLO producer with frame-time and stale-result tracking."""

from __future__ import annotations

import math
import os
import copy
import threading
import time
from typing import Any


class DetectionLoop:
    """Continuously infer on the latest frame from one shared source.

    Timestamp-aware sources may implement ``read_with_timestamp()`` and
    return ``(frame, capture_timestamp)``. Other FrameSources use the start of
    ``read()`` as the best available capture-time estimate.
    """

    def __init__(self, source: Any, detector: Any, fps: float | None = None,
                 stale_s: float = 2.0) -> None:
        self.source = source
        self.detector = detector
        self.fps = max(0.1, float(fps if fps is not None else os.getenv("YOLO_FPS", "3")))
        self.stale_s = max(0.0, float(stale_s))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._latest: list[Any] = []
        self._frame_timestamp: float | None = None
        self._available = False

    @property
    def available(self) -> bool:
        with self._lock:
            timestamp = self._frame_timestamp
            available = self._available
        return bool(
            available
            and self.detector.available
            and timestamp is not None
            and 0.0 <= time.monotonic() - timestamp <= self.stale_s
        )

    def latest(self) -> tuple[list[Any], float | None]:
        """Return the newest inference and the capture time of its frame."""
        with self._lock:
            results = [
                item.model_copy(deep=True)
                if hasattr(item, "model_copy")
                else copy.deepcopy(item)
                for item in self._latest
            ]
            return results, self._frame_timestamp

    def start(self) -> None:
        """Start at most one producer thread."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not self.detector.available:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="yolo-detection-loop",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop the producer and clear its result; safe to call repeatedly."""
        self._stop.set()
        try:
            self.source.close()
        except Exception:
            pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(2.0, min(5.0, 3.0 / self.fps)))
        with self._lifecycle_lock:
            if thread is None or not thread.is_alive():
                self._thread = None
        with self._lock:
            self._available = False
            self._latest = []
            self._frame_timestamp = None

    def _read_frame(self) -> tuple[Any, float]:
        reader = getattr(self.source, "read_with_timestamp", None)
        if callable(reader):
            frame, timestamp = reader()
        else:
            timestamp = time.monotonic()
            frame = self.source.read()
        timestamp = float(timestamp)
        if not math.isfinite(timestamp):
            raise ValueError("camera frame timestamp is invalid")
        return frame, timestamp

    def _clear_latest(self) -> None:
        with self._lock:
            self._available = False
            self._latest = []
            self._frame_timestamp = None

    def _run(self) -> None:
        retry_delay = 0.5
        source_open = False
        try:
            while not self._stop.is_set():
                try:
                    self.source.open()
                    source_open = True
                    while not self._stop.is_set():
                        started = time.monotonic()
                        frame, frame_timestamp = self._read_frame()
                        detections = self.detector.detect(frame)
                        with self._lock:
                            self._latest = list(detections)
                            self._frame_timestamp = frame_timestamp
                            self._available = True
                        retry_delay = 0.5
                        self._stop.wait(max(0.0, 1.0 / self.fps - (time.monotonic() - started)))
                except Exception:
                    self._clear_latest()
                    # close() is also needed after a partially failed open.
                    try:
                        self.source.close()
                    except Exception:
                        pass
                    source_open = False
                if not self._stop.is_set():
                    self._stop.wait(retry_delay)
                    retry_delay = min(retry_delay * 2, 5.0)
        finally:
            self._clear_latest()
            try:
                self.source.close()
            except Exception:
                pass
