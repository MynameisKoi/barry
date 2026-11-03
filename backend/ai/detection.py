"""AI Detection Module for BARRY.

Handles lightweight object detection and generates detection alerts
for consumption by Meta Quest VR command center and SQLite audit logs.
"""

from datetime import datetime, timezone
import random
from typing import Any, Dict, List, Optional
from backend.models import DetectionAlert


class AIDetectionService:
    """Manages visual inference and simulated AI alerts."""

    def __init__(self) -> None:
        self.sample_objects = [
            ("person", 0.94, "Restricted Corridor A"),
            ("hazard_cone", 0.88, "Zone B - Perimeter"),
            ("unauthorized_backpack", 0.82, "Zone A - South Gate"),
            ("spill_hazard", 0.79, "Inspection Sector 3"),
            ("person", 0.91, "Restricted Server Room"),
        ]

    def generate_simulated_detection(self, zone: str = "Restricted Area") -> DetectionAlert:
        """Generates a realistic simulated detection event for VR preview."""
        obj, base_conf, default_zone = random.choice(self.sample_objects)
        confidence = round(base_conf + random.uniform(-0.05, 0.04), 2)
        confidence = min(0.99, max(0.50, confidence))

        alert = DetectionAlert(
            type="detection",
            object=obj,
            confidence=confidence,
            zone=zone if zone else default_zone,
            timestamp=datetime.now(timezone.utc).isoformat(),
            bounding_box=[
                round(random.uniform(0.1, 0.3), 2),
                round(random.uniform(0.1, 0.3), 2),
                round(random.uniform(0.4, 0.7), 2),
                round(random.uniform(0.4, 0.8), 2),
            ],
        )
        return alert

    def process_frame(self, frame_data: Any, zone: str = "Restricted Area") -> List[DetectionAlert]:
        """Processes an image frame through object detection model.

        When OpenCV / lightweight YOLO is configured, this runs laptop inference.
        Returns list of detection alerts.
        """
        # Hook for Day 6 OpenCV / YOLO inference
        if frame_data is None:
            return [self.generate_simulated_detection(zone=zone)]
        return []


# Global default detection service
detection_service = AIDetectionService()
