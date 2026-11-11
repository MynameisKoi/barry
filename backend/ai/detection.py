"""Real object detection and the explicit demo-only simulated alert."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import random
import threading
import asyncio
import time
from typing import List, Optional

import numpy as np

from backend.ai.vision import DetectionLoop, Detector
from backend.models import DetectionAlert

logger = logging.getLogger(__name__)
LLM_FRAME_INTERVAL_S = max(1.0, float(os.getenv("LLM_CAMERA_FRAME_INTERVAL_S", "5")))
DETECTION_CONTEXT_TTL_S = LLM_FRAME_INTERVAL_S + 1.0


class AIDetectionService:
    def __init__(self, model_path: Optional[str] = None) -> None:
        self.model_path = model_path or os.getenv("YOLO_MODEL_PATH", "models/yolo11n.onnx")
        self.sample_objects = [
            ("person", 0.94, "Restricted Corridor A"),
            ("hazard_cone", 0.88, "Zone B - Perimeter"),
            ("unauthorized_backpack", 0.82, "Zone A - South Gate"),
            ("spill_hazard", 0.79, "Inspection Sector 3"),
            ("person", 0.91, "Restricted Server Room"),
        ]
        self.detector = Detector(self.model_path)
        self._loop: DetectionLoop | None = None
        self._lock = threading.Lock()
        self._latest: list[DetectionAlert] = []
        self._updated_at = 0.0

    def attach_source(self, source) -> None:
        with self._lock:
            if self._loop:
                self._loop.stop()
            self._loop = DetectionLoop(source, self.detector)
            self._loop.start()

    def detections(self, zone: str = "Restricted Area") -> List[DetectionAlert]:
        with self._lock:
            loop = self._loop
            latest, updated = list(self._latest), self._updated_at
        if loop:
            return loop.latest(zone)
        if not updated or time.monotonic() - updated > DETECTION_CONTEXT_TTL_S:
            return []
        return [item.model_copy(update={"zone": zone}) for item in latest]

    def status(self) -> str:
        with self._lock:
            loop = self._loop
        if loop:
            return "available" if loop.available else "unavailable"
        return "available" if self._updated_at and time.monotonic()-self._updated_at <= DETECTION_CONTEXT_TTL_S else "unavailable"

    def process_frame(self, frame: Optional[np.ndarray], zone: str = "Restricted Area") -> List[DetectionAlert]:
        if frame is None or self.detector.session is None:
            with self._lock:
                self._latest, self._updated_at = [], 0.0
            return []
        try:
            detections = self.detector.detect(frame, zone)
            with self._lock:
                self._latest, self._updated_at = detections, time.monotonic()
            return detections
        except Exception:
            logger.exception("YOLO inference failed; returning no detections")
            with self._lock:
                self._latest, self._updated_at = [], 0.0
            return []

    async def run_camera(self, camera, fps: float = 1.0 / LLM_FRAME_INTERVAL_S) -> None:
        """Sample the shared camera snapshot; never opens a second device handle."""
        import cv2
        import numpy as np

        interval = 1.0 / max(0.1, fps)
        while True:
            if not self.detector.available:
                with self._lock:
                    self._latest, self._updated_at = [], 0.0
                await asyncio.sleep(interval)
                continue
            started = time.monotonic()
            try:
                frame = await asyncio.to_thread(camera.latest_frame)
                image = cv2.imdecode(np.frombuffer(frame.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError("camera JPEG could not be decoded")
                await asyncio.to_thread(self.process_frame, image, "Robot camera")
            except asyncio.CancelledError:
                raise
            except Exception:
                with self._lock:
                    self._latest, self._updated_at = [], 0.0
            await asyncio.sleep(max(0.0, interval - (time.monotonic()-started)))

    def generate_simulated_detection(self, zone: str = "Restricted Area") -> DetectionAlert:
        obj, base_conf, default_zone = random.choice(self.sample_objects)
        return DetectionAlert(type="detection", object=obj,
            confidence=min(0.99, max(0.50, round(base_conf + random.uniform(-0.05, 0.04), 2))),
            zone=zone or default_zone, timestamp=datetime.now(timezone.utc).isoformat(),
            bounding_box=[0.1, 0.1, 0.8, 0.8], simulated=True)


detection_service = AIDetectionService()
