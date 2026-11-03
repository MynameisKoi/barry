"""Camera stream manager for BARRY.

Provides real-time MJPEG video streaming, snapshot generation, and
an interactive synthetic inspection HUD fallback when physical cameras
are not yet attached.
"""

import asyncio
from datetime import datetime, timezone
import math
import os
import threading
import time
from typing import Generator, List, Optional, Tuple
import cv2
import numpy as np

CAMERA_SOURCE = os.getenv("CAMERA_SOURCE", "auto")  # 'auto', 'webcam', 'synthetic'
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
STREAM_WIDTH = 640
STREAM_HEIGHT = 480
TARGET_FPS = 20


class CameraStreamer:
    """Thread-safe camera streamer supporting physical cameras and synthetic feeds."""

    def __init__(self, source: str = CAMERA_SOURCE, cam_index: int = CAMERA_INDEX) -> None:
        self.source = source
        self.cam_index = cam_index
        self.cap: Optional[cv2.VideoCapture] = None
        self.running: bool = True
        self.lock = threading.Lock()
        self.tick: int = 0
        self.detected_boxes: List[dict] = []
        self.current_frame: np.ndarray = self._create_synthetic_frame(0)
        self._init_source()

    def _init_source(self) -> None:
        """Attempts physical camera capture; falls back gracefully to synthetic."""
        if self.source in ("auto", "webcam"):
            try:
                cap = cv2.VideoCapture(self.cam_index)
                if cap.isOpened():
                    ret, test_frame = cap.read()
                    if ret and test_frame is not None:
                        self.cap = cap
                        self.source = "webcam"
                        return
                    cap.release()
            except Exception:
                pass
        self.cap = None
        self.source = "synthetic"

    def _create_synthetic_frame(self, tick: int) -> np.ndarray:
        """Generates a high-tech robotic inspection HUD overlay with moving target."""
        # Dark grid background
        frame = np.zeros((STREAM_HEIGHT, STREAM_WIDTH, 3), dtype=np.uint8)
        frame[:] = (18, 22, 26)  # Deep cyber slate

        # Draw tech grid lines
        grid_step = 40
        for x in range(0, STREAM_WIDTH, grid_step):
            cv2.line(frame, (x, 0), (x, STREAM_HEIGHT), (28, 35, 42), 1)
        for y in range(0, STREAM_HEIGHT, grid_step):
            cv2.line(frame, (0, y), (STREAM_WIDTH, y), (28, 35, 42), 1)

        # Reticle / Crosshair center
        cx, cy = STREAM_WIDTH // 2, STREAM_HEIGHT // 2
        cv2.circle(frame, (cx, cy), 50, (0, 200, 255), 1)
        cv2.circle(frame, (cx, cy), 4, (0, 255, 128), -1)
        cv2.line(frame, (cx - 70, cy), (cx - 20, cy), (0, 200, 255), 1)
        cv2.line(frame, (cx + 20, cy), (cx + 70, cy), (0, 200, 255), 1)
        cv2.line(frame, (cx, cy - 70), (cx, cy - 20), (0, 200, 255), 1)
        cv2.line(frame, (cx, cy + 20), (cx, cy + 70), (0, 200, 255), 1)

        # Corner brackets
        bracket_len = 25
        # Top-left
        cv2.line(frame, (20, 20), (20 + bracket_len, 20), (0, 255, 180), 2)
        cv2.line(frame, (20, 20), (20, 20 + bracket_len), (0, 255, 180), 2)
        # Top-right
        cv2.line(frame, (STREAM_WIDTH - 20, 20), (STREAM_WIDTH - 20 - bracket_len, 20), (0, 255, 180), 2)
        cv2.line(frame, (STREAM_WIDTH - 20, 20), (STREAM_WIDTH - 20, 20 + bracket_len), (0, 255, 180), 2)
        # Bottom-left
        cv2.line(frame, (20, STREAM_HEIGHT - 20), (20 + bracket_len, STREAM_HEIGHT - 20), (0, 255, 180), 2)
        cv2.line(frame, (20, STREAM_HEIGHT - 20), (20, STREAM_HEIGHT - 20 - bracket_len), (0, 255, 180), 2)
        # Bottom-right
        cv2.line(frame, (STREAM_WIDTH - 20, STREAM_HEIGHT - 20), (STREAM_WIDTH - 20 - bracket_len, STREAM_HEIGHT - 20), (0, 255, 180), 2)
        cv2.line(frame, (STREAM_WIDTH - 20, STREAM_HEIGHT - 20), (STREAM_WIDTH - 20, STREAM_HEIGHT - 20 - bracket_len), (0, 255, 180), 2)

        # Simulated moving inspection subject (e.g. human / obstacle in sector)
        angle = (tick % 360) * (math.pi / 180)
        target_x = int(cx + 120 * math.cos(angle * 1.5))
        target_y = int(cy + 60 * math.sin(angle * 2.0))
        box_w, box_h = 70, 110

        # Draw inspection subject placeholder
        pt1 = (target_x - box_w // 2, target_y - box_h // 2)
        pt2 = (target_x + box_w // 2, target_y + box_h // 2)
        cv2.rectangle(frame, pt1, pt2, (255, 140, 0), 2)
        cv2.putText(frame, "TRACKED_SUBJECT: PERSON", (pt1[0], pt1[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 140, 0), 1)

        # Render dynamically detected bounding boxes from AI
        with self.lock:
            boxes = list(self.detected_boxes)
        for b in boxes:
            bx1, by1 = int(b.get("xmin", 0)), int(b.get("ymin", 0))
            bx2, by2 = int(b.get("xmax", 0)), int(b.get("ymax", 0))
            label = f"{b.get('label', 'object').upper()} {int(b.get('score', 0)*100)}%"
            cv2.rectangle(frame, (bx1, by1), (bx2, by2), (0, 60, 255), 2)
            cv2.putText(frame, label, (bx1, max(15, by1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 60, 255), 1)

        # HUD Header & Status
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        cv2.putText(frame, "BARRY ROBOTIC INSPECTION // CAM-01", (25, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 200), 2)
        cv2.putText(frame, f"MODE: {self.source.upper()}  |  FPS: {TARGET_FPS}  |  {now_str}", (25, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 200, 210), 1)

        # Bottom Telemetry bar
        cv2.putText(frame, f"SIM_TICK: {tick:06d}   LATENCY: ~12ms   STATUS: NOMINAL", (25, STREAM_HEIGHT - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 120), 1)

        return frame

    def update_detected_boxes(self, boxes: List[dict]) -> None:
        """Updates bounding boxes to be rendered onto the stream."""
        with self.lock:
            self.detected_boxes = boxes

    def capture_frame(self) -> np.ndarray:
        """Captures the next frame from webcam or generates synthetic tick."""
        self.tick += 1
        if self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret and frame is not None:
                frame = cv2.resize(frame, (STREAM_WIDTH, STREAM_HEIGHT))
                with self.lock:
                    self.current_frame = frame
                return frame
        # Fallback to synthetic
        frame = self._create_synthetic_frame(self.tick)
        with self.lock:
            self.current_frame = frame
        return frame

    def get_latest_frame(self) -> np.ndarray:
        """Returns the most recent frame."""
        with self.lock:
            return self.current_frame.copy()

    def get_jpeg_bytes(self) -> bytes:
        """Encodes latest frame to JPEG."""
        frame = self.capture_frame()
        ret, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not ret:
            return b""
        return jpeg.tobytes()

    def stream_generator(self) -> Generator[bytes, None, None]:
        """Generator yielding MJPEG multipart chunks."""
        self.running = True
        frame_delay = 1.0 / TARGET_FPS
        while self.running:
            t0 = time.time()
            jpeg_bytes = self.get_jpeg_bytes()
            if jpeg_bytes:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg_bytes + b"\r\n"
                )
            elapsed = time.time() - t0
            sleep_time = max(0.005, frame_delay - elapsed)
            time.sleep(sleep_time)

    def close(self) -> None:
        """Releases video resources."""
        self.running = False
        if self.cap:
            self.cap.release()
            self.cap = None


# Global camera streamer instance
camera_streamer = CameraStreamer()
