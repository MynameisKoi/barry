"""The dog MJPEG source feeds the single background detector in simulation."""

from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import cv2
import numpy as np

from backend.ai.detection import AIDetectionService, CameraServiceDetectionSource
from backend.camera.config import CameraConfig
from backend.camera.factory import build_camera_service
from backend.camera.remote_pi import RemotePiCameraSource
from backend.models import DetectionAlert


def _jpeg_frame() -> bytes:
    # A visible, non-uniform frame lets this integration test exercise JPEG
    # decoding and the shared raw-frame path without using a physical camera.
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    cv2.rectangle(image, (25, 20), (135, 105), (0, 180, 255), -1)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


class _FrameDetector:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        assert frame.shape == (120, 160, 3)
        return [DetectionAlert(
            object="chair", confidence=0.9, zone="Robot camera",
            timestamp="captured", bounding_box=[0.1, 0.2, 0.8, 0.9],
        )]


def test_simulated_dog_mjpeg_reaches_live_detection_api(monkeypatch):
    jpeg = _jpeg_frame()
    stopped = threading.Event()

    class MjpegHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            part = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            try:
                while not stopped.is_set():
                    self.wfile.write(part)
                    self.wfile.flush()
                    time.sleep(0.03)
            except OSError:
                pass  # CameraService closes this local stream during cleanup.

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), MjpegHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{server.server_port}/video_feed"

    monkeypatch.setenv("ROBOT_MODE", "simulation")
    monkeypatch.setenv("CAMERA_SOURCE", "dog")
    monkeypatch.setenv("DOG_VIDEO_URL", url)
    monkeypatch.delenv("CAMERA_DEVICE", raising=False)
    camera = build_camera_service(CameraConfig(width=160, height=120, target_fps=10))
    assert isinstance(camera._source, RemotePiCameraSource)
    assert camera._source.url == url

    detector = _FrameDetector()
    service = AIDetectionService(detector=detector)
    import backend.main as main
    monkeypatch.setattr(main, "detection_service", service)
    try:
        camera.start()
        service.attach_source(CameraServiceDetectionSource(camera), camera=camera, fps=3)
        deadline = time.monotonic() + 3
        result = []
        while time.monotonic() < deadline:
            result = service.detections()
            if result:
                break
            time.sleep(0.02)

        assert service.status() == "available"
        assert result[0]["label"] == "chair"
        assert result[0]["age_s"] >= 0
        assert result[0]["simulated"] is False
        assert detector.calls >= 1
        api_result = asyncio.run(main.get_live_detections())
        assert api_result["status"] == "available"
        assert api_result["detections"][0]["label"] == "chair"
    finally:
        service.stop()
        camera.stop()
        stopped.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)
