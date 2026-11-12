"""Tests for the legacy synthetic streamer and AI visual detection.

The HTTP camera API is covered by backend/camera/test_router.py."""

import pytest
from fastapi.testclient import TestClient
from backend.ai.detection import detection_service
from backend.auth.flipper_listener import UID_REGEX
from backend.camera.streamer import camera_streamer
from backend.main import app


def test_camera_stream_chunk_generator():
    """Verifies stream generator produces MJPEG boundary chunks."""
    gen = camera_streamer.stream_generator()
    chunk = next(gen)
    assert b"--frame" in chunk
    assert b"Content-Type: image/jpeg" in chunk
    assert len(chunk) > 1000


def test_ai_detection_processing_never_fabricates_without_model():
    """No model means no detections, even when a frame is present."""
    frame = camera_streamer.capture_frame()
    from backend.ai.detection import AIDetectionService
    service = AIDetectionService(model_path="/missing/yolo11n.onnx")
    assert frame is not None
    assert service.detections(zone="Sector-Delta") == []


def test_flipper_uid_regex():
    """Verifies regex extraction of various Flipper Zero NFC CLI outputs."""
    line1 = "Found Mifare Classic 1k, UID: 04 A2 3B 4C 5D 6E 7F"
    match1 = UID_REGEX.search(line1)
    assert match1 is not None
    assert match1.group(1).replace(" ", "") == "04A23B4C5D6E7F"

    line2 = "ISO14443A tag UID: 04:11:22:33:44:55:66 (7 bytes)"
    match2 = UID_REGEX.search(line2)
    assert match2 is not None


def test_cockpit_endpoint():
    """Verifies that /cockpit returns valid HTML with cybernetic command UI."""
    with TestClient(app) as client:
        resp = client.get("/cockpit")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "BARRY // COCKPIT" in resp.text
        assert "DOGZILLA FORWARD OPTICS" in resp.text


def test_cockpit_vr_endpoint():
    """Verifies that /cockpit/vr returns WebXR 3D cockpit aligned with Unity Barry.exe layout."""
    with TestClient(app) as client:
        resp = client.get("/cockpit/vr")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "EYE_LEVEL" in resp.text
        assert "SIDE_PANEL_YAW" in resp.text
        assert "headerPanel" in resp.text
        assert "emergencyBanner" in resp.text
        assert "BARRY" in resp.text

    line3 = "Tag UID: A1:B2:C3:D4"
    match3 = UID_REGEX.search(line3)
    assert match3 is not None
