"""Camera source selection: remote robot mode reads the dog's MJPEG feed, never the laptop webcam."""

import pytest

from backend.camera.factory import build_camera_service, dog_video_url
from backend.camera.remote_pi import RemotePiCameraSource
from backend.camera.synthetic import SyntheticSource
from backend.camera.yahboom import YahboomSource


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("CAMERA_SOURCE", "CAMERA_DEVICE", "ROBOT_MODE", "ROBOT_REMOTE_URL", "DOG_VIDEO_URL", "CAMERA_PI_URL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def source_of(service):
    return service._source if hasattr(service, "_source") else service.source


def test_remote_mode_auto_uses_dog_stream(clean_env):
    clean_env.setenv("ROBOT_MODE", "remote")
    clean_env.setenv("ROBOT_REMOTE_URL", "http://192.168.0.174:8100")
    src = source_of(build_camera_service())
    assert isinstance(src, RemotePiCameraSource)
    assert src.url == "http://192.168.0.174:6500/video_feed"
    assert src.api_key == ""


def test_explicit_dog_source_and_url(clean_env):
    clean_env.setenv("CAMERA_SOURCE", "dog")
    clean_env.setenv("DOG_VIDEO_URL", "http://10.0.0.9:6500/video_feed")
    src = source_of(build_camera_service())
    assert isinstance(src, RemotePiCameraSource) and src.url == "http://10.0.0.9:6500/video_feed"


def test_synthetic_wins_and_local_modes_unchanged(clean_env):
    clean_env.setenv("ROBOT_MODE", "remote")
    clean_env.setenv("CAMERA_DEVICE", "synthetic")
    assert isinstance(source_of(build_camera_service()), SyntheticSource)
    clean_env.delenv("CAMERA_DEVICE")
    clean_env.setenv("ROBOT_MODE", "simulation")
    assert isinstance(source_of(build_camera_service()), YahboomSource)


def test_dog_video_url_default(clean_env):
    assert dog_video_url() == "http://192.168.137.236:6500/video_feed"
