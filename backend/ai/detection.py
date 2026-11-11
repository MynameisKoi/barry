"""Real object detection and the explicit demo-only simulated alert."""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import random
import threading
from typing import List, Optional

import numpy as np

from backend.ai.vision import DetectionLoop, Detector
from backend.models import DetectionAlert

logger = logging.getLogger(__name__)


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

    def attach_source(self, source) -> None:
        with self._lock:
            if self._loop:
                self._loop.stop()
            self._loop = DetectionLoop(source, self.detector)
            self._loop.start()

    def detections(self, zone: str = "Restricted Area") -> List[DetectionAlert]:
        with self._lock:
            loop = self._loop
        return loop.latest(zone) if loop else []

    def status(self) -> str:
        with self._lock:
            loop = self._loop
        return "available" if loop and loop.available else "unavailable"

    def process_frame(self, frame: Optional[np.ndarray], zone: str = "Restricted Area") -> List[DetectionAlert]:
        if frame is None or self.detector.session is None:
            return []
        try:
            return self.detector.detect(frame, zone)
        except Exception:
            logger.exception("YOLO inference failed; returning no detections")
            return []

    def generate_simulated_detection(self, zone: str = "Restricted Area") -> DetectionAlert:
        obj, base_conf, default_zone = random.choice(self.sample_objects)
        return DetectionAlert(type="detection", object=obj,
            confidence=min(0.99, max(0.50, round(base_conf + random.uniform(-0.05, 0.04), 2))),
            zone=zone or default_zone, timestamp=datetime.now(timezone.utc).isoformat(),
            bounding_box=[0.1, 0.1, 0.8, 0.8], simulated=True)


detection_service = AIDetectionService()
