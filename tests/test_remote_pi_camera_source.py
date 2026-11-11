import cv2
import httpx
import numpy as np
import pytest
import time

from backend.camera.interface import FrameCaptureError
from backend.camera.config import CameraConfig
from backend.camera.factory import build_camera_service
from backend.camera.remote_pi import RemotePiCameraSource
from backend.camera.service import CameraService


def jpeg_frame(color):
    ok, encoded = cv2.imencode(".jpg", np.full((24, 32, 3), color, dtype=np.uint8))
    assert ok
    return encoded.tobytes()


def test_factory_selects_remote_pi_source_from_environment(monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "pi")
    monkeypatch.setenv("CAMERA_PI_URL", "http://pi:8100/api/v1/camera/stream")
    monkeypatch.setenv("ROBOT_API_KEY", "laptop-key")
    service = build_camera_service(CameraConfig(device="synthetic"))
    assert isinstance(service._source, RemotePiCameraSource)
    assert service._source.url == "http://pi:8100/api/v1/camera/stream"
    assert service._source.api_key == "laptop-key"


def test_remote_pi_source_decodes_mjpeg_and_sends_robot_key():
    frame = jpeg_frame((0, 0, 255))
    seen = []
    def handler(request):
        seen.append(request.headers.get("x-robot-key"))
        return httpx.Response(200, content=b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n--frame\r\n")
    source = RemotePiCameraSource("http://pi/api/v1/camera/stream", "laptop-key", transport=httpx.MockTransport(handler))
    source.open()
    image = source.read()
    assert image.shape == (24, 32, 3)
    assert seen == ["laptop-key"]
    source.close()


def test_remote_pi_source_can_reopen_after_stream_drop():
    frame = jpeg_frame((0, 255, 0))
    attempts = 0
    def handler(_request):
        nonlocal attempts
        attempts += 1
        content = b"broken-stream" if attempts == 1 else frame
        return httpx.Response(200, content=content)
    source = RemotePiCameraSource("http://pi/stream", "key", transport=httpx.MockTransport(handler))
    source.open()
    with pytest.raises(FrameCaptureError):
        source.read()
    source.close()
    source.open()
    assert source.read().shape == (24, 32, 3)
    assert attempts == 2
    source.close()


def test_camera_service_reconnects_remote_source_after_stream_drop():
    frame = jpeg_frame((255, 0, 0))
    attempts = 0
    def handler(_request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, content=b"bad" if attempts == 1 else frame)

    source = RemotePiCameraSource("http://pi/stream", "key", transport=httpx.MockTransport(handler))
    service = CameraService(source, CameraConfig(device="synthetic", width=160, height=120, target_fps=15))
    # The first source frame drops during startup; CameraService owns recovery.
    try:
        service.start()
    except Exception:
        pass
    deadline = time.monotonic() + 3
    while not service.status().connected and time.monotonic() < deadline:
        time.sleep(.02)
    assert service.status().connected
    assert attempts >= 2
    service.stop()
