"""Live YOLO detections for the command gateway: on demand, from the newest shared-camera frame.

* **Single source of truth:** ``CommandGateway.detections()`` / ``status()["detector"]`` read this. There
  is no wrapper gateway and no second camera handle: it uses the shared ``CameraService`` frame.
* **On demand and fresh:** a call runs inference on the newest frame, with a short cache
  (``YOLO_CACHE_S``, default 1.0 s) so repeated calls in one turn don't re-run it. Concurrent calls
  share one inference. Inference (JPEG decode + ONNX) runs in a worker thread, never on the event loop.
* **Age:** every result carries ``age_s`` = now - frame capture time. Nothing older than
  ``YOLO_MAX_AGE_S`` (default 2.0) is returned: ``[]`` instead.
* **Never fabricated:** no model, no camera, a stale frame or an inference error all give ``[]``. The
  contour/random fallbacks and the simulated demo alert are never used here.

Detector states for ``status()``: ``available`` | ``unavailable`` | ``no_camera`` | ``no_model``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

AVAILABLE, UNAVAILABLE, NO_CAMERA, NO_MODEL = "available", "unavailable", "no_camera", "no_model"
CACHE_ENV, MAX_AGE_ENV = "YOLO_CACHE_S", "YOLO_MAX_AGE_S"
ZONE = "Robot camera"


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    if value != value or value in (float("inf"), float("-inf")):
        return default
    return min(max(value, low), high)


@dataclass
class _Cached:
    frame_timestamp: float
    inferred_at: float
    results: list[dict[str, Any]]


class LiveDetections:
    def __init__(self, camera: Callable[[], Any], detector: Any, *, cache_s: float | None = None,
                 max_age_s: float | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        """``camera()`` returns the shared camera service (or None); ``detector`` is a
        ``backend.ai.vision.Detector`` (or None). ``clock`` must match ``Frame.timestamp`` (monotonic)."""
        self._camera = camera
        self._detector = detector
        self._cache_s = _env_float(CACHE_ENV, 1.0, 0.0, 10.0) if cache_s is None else cache_s
        self._max_age_s = _env_float(MAX_AGE_ENV, 2.0, 0.1, 30.0) if max_age_s is None else max_age_s
        self._clock = clock
        self._cached: _Cached | None = None
        self._lock = asyncio.Lock()
        self._last_inference_failed = False

    # ------------------------------------------------------------------ state

    def _frame(self):
        """Newest frame, or raises. Never blocks: the camera service only takes a short lock."""
        camera = self._camera()
        if camera is None:
            raise _NoCamera()
        try:
            return camera.latest_frame()
        except Exception as exc:  # CameraUnavailableError or any camera fault
            raise _NoCamera() from exc

    def state(self) -> str:
        """The real detector state right now. Cheap: it never runs inference."""
        if self._detector is None or not getattr(self._detector, "available", False):
            return NO_MODEL
        try:
            frame = self._frame()
        except _NoCamera:
            return NO_CAMERA
        if self._clock() - frame.timestamp > self._max_age_s or self._last_inference_failed:
            return UNAVAILABLE
        return AVAILABLE

    # ------------------------------------------------------------------ detections

    async def detections(self) -> list[dict[str, Any]]:
        if self.state() in (NO_MODEL, NO_CAMERA):
            return []
        async with self._lock:  # concurrent callers share one inference
            try:
                frame = self._frame()
            except _NoCamera:
                return []
            now = self._clock()
            if now - frame.timestamp > self._max_age_s:
                return []  # stale frame: do not run inference, never return old results

            cached = self._cached
            if cached and now - cached.inferred_at <= self._cache_s:
                return self._aged(cached, now)  # same turn: don't re-run inference (age_s keeps growing)

            try:
                results = await asyncio.to_thread(self._infer, frame.jpeg)
            except Exception as exc:
                self._last_inference_failed = True
                self._cached = None
                logger.warning("Live detection failed (%s); returning no detections.", type(exc).__name__)
                return []
            self._last_inference_failed = False
            self._cached = _Cached(frame.timestamp, self._clock(), results)
            return self._aged(self._cached, self._clock())

    def _aged(self, cached: _Cached, now: float) -> list[dict[str, Any]]:
        age = now - cached.frame_timestamp
        if age > self._max_age_s:
            return []
        return [{**item, "age_s": round(max(age, 0.0), 2)} for item in cached.results]

    def _infer(self, jpeg: bytes) -> list[dict[str, Any]]:
        """Runs in a worker thread: decode the JPEG and run YOLO. Labels come from the fixed COCO table."""
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("camera frame could not be decoded")
        results = []
        for alert in self._detector.detect(image, ZONE):
            results.append({
                "label": alert.object,
                "confidence": round(float(alert.confidence), 2),
                "box": [round(float(v), 3) for v in (alert.bounding_box or [])],  # [ymin, xmin, ymax, xmax], 0-1
            })
        return results


class _NoCamera(Exception):
    pass
