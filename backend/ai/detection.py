"""Background YOLO detection service and explicit demo-only simulated alert."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import random
import threading
import time
from typing import Any

from backend.ai.vision import DetectionLoop, Detector
from backend.camera.interface import FrameCaptureError
from backend.camera.source import FrameSource
from backend.models import DetectionAlert

logger = logging.getLogger(__name__)

AVAILABLE = "available"
UNAVAILABLE = "unavailable"
NO_CAMERA = "no_camera"
NO_MODEL = "no_model"


def _max_age_from_env() -> float:
    try:
        value = float(os.getenv("YOLO_MAX_AGE_S", "2.0"))
    except (TypeError, ValueError):
        return 2.0
    if value != value or value in (float("inf"), float("-inf")):
        return 2.0
    return min(max(value, 0.1), 30.0)


class CameraServiceDetectionSource(FrameSource):
    """Read the shared camera's raw frame and capture timestamp atomically.

    The camera service remains the only owner of the physical source. This
    adapter snapshots its published raw frame and matching ``Frame.timestamp``
    under the service lock so the inference loop never pairs different frames.
    """

    def __init__(self, camera: Any) -> None:
        self._camera = camera

    @property
    def name(self) -> str:
        return "camera-service-yolo"

    def open(self) -> None:
        pass

    def read_with_timestamp(self):
        camera = self._camera
        with camera._condition:
            frame = camera._latest
            raw = camera._latest_raw
            if not camera._connected or frame is None or raw is None:
                raise FrameCaptureError("no shared camera frame is available")
            return raw.copy(), frame.timestamp

    def read(self):
        return self.read_with_timestamp()[0]

    def close(self) -> None:
        # The camera service owns its lifecycle; stopping inference must not
        # close or reopen the shared camera.
        pass


class AIDetectionService:
    """Own one background DetectionLoop and expose only its latest results."""

    def __init__(self, model_path: str | None = None, *, detector: Any = None,
                 max_age_s: float | None = None) -> None:
        self.detector = detector if detector is not None else Detector(
            model_path or os.getenv("YOLO_MODEL_PATH", "models/yolo11n.onnx")
        )
        self.max_age_s = _max_age_from_env() if max_age_s is None else max(0.1, float(max_age_s))
        self.sample_objects = [
            ("person", 0.94, "Restricted Corridor A"),
            ("hazard_cone", 0.88, "Zone B - Perimeter"),
            ("unauthorized_backpack", 0.82, "Zone A - South Gate"),
            ("spill_hazard", 0.79, "Inspection Sector 3"),
            ("person", 0.91, "Restricted Server Room"),
        ]
        self._loop: DetectionLoop | None = None
        self._camera: Any = None
        self._lock = threading.RLock()

    def attach_source(self, source: FrameSource, *, camera: Any = None,
                      fps: float | None = None) -> None:
        """Replace the background producer, stopping the previous one first."""
        with self._lock:
            previous = self._loop
            self._loop = None
            self._camera = camera
        if previous is not None:
            previous.stop()

        loop = DetectionLoop(source, self.detector, fps=fps, stale_s=self.max_age_s)
        with self._lock:
            self._loop = loop
            self._camera = camera
        loop.start()

    def stop(self) -> None:
        with self._lock:
            loop = self._loop
            self._loop = None
            self._camera = None
        if loop is not None:
            loop.stop()

    def status(self) -> str:
        if not self.detector.available:
            return NO_MODEL
        with self._lock:
            loop, camera = self._loop, self._camera
        if camera is None:
            return NO_CAMERA
        try:
            camera.latest_frame()
        except Exception:
            return NO_CAMERA
        if loop is None:
            return UNAVAILABLE
        _, frame_timestamp = loop.latest()
        if frame_timestamp is None or time.monotonic() - frame_timestamp > self.max_age_s:
            return UNAVAILABLE
        return AVAILABLE if loop.available else UNAVAILABLE

    def detections(self, zone: str = "Robot camera") -> list[dict[str, Any]]:
        """Return fresh results from the loop without running another inference."""
        if self.status() != AVAILABLE:
            return []
        with self._lock:
            loop = self._loop
        if loop is None:
            return []
        results, frame_timestamp = loop.latest()
        if frame_timestamp is None:
            return []
        now = time.monotonic()
        age = now - frame_timestamp
        if age < 0 or age > self.max_age_s:
            return []

        detections = []
        for result in results:
            if isinstance(result, dict):
                label = result.get("label", result.get("object", ""))
                confidence = result.get("confidence", 0.0)
                box = result.get("box", result.get("bounding_box", []))
                timestamp = result.get("timestamp")
            else:
                label = result.object
                confidence = result.confidence
                box = result.bounding_box or []
                timestamp = result.timestamp
            box = [float(value) for value in box]
            detections.append({
                "type": "detection",
                "label": str(label),
                "object": str(label),
                "confidence": round(float(confidence), 2),
                "box": box,
                "bounding_box": box,
                "age_s": round(age, 2),
                "zone": zone,
                "timestamp": timestamp,
                "simulated": False,
            })
        return detections

    def generate_simulated_detection(self, zone: str = "Restricted Area") -> DetectionAlert:
        """Explicit demo-only alert; it is never added to real detection results."""
        obj, base_conf, default_zone = random.choice(self.sample_objects)
        return DetectionAlert(type="detection", object=obj,
            confidence=min(0.99, max(0.50, round(base_conf + random.uniform(-0.05, 0.04), 2))),
            zone=zone or default_zone, timestamp=datetime.now(timezone.utc).isoformat(),
            bounding_box=[0.1, 0.1, 0.8, 0.8], simulated=True)


detection_service = AIDetectionService()
