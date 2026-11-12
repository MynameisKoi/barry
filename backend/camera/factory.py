"""Wiring helper that assembles the camera stack from configuration."""

from __future__ import annotations

from typing import Optional
import os

from .config import CameraConfig
from .service import CameraService
from .source import FrameSource
from .synthetic import SyntheticSource
from .yahboom import YahboomSource


def dog_video_url() -> str:
    """URL of the dog's Yahboom MJPEG feed: DOG_VIDEO_URL, else port 6500 on the ROBOT_REMOTE_URL host."""
    explicit = os.getenv("DOG_VIDEO_URL", "").strip()
    if explicit:
        return explicit
    from urllib.parse import urlparse

    host = urlparse(os.getenv("ROBOT_REMOTE_URL", "")).hostname
    return f"http://{host}:6500/video_feed" if host else "http://192.168.137.236:6500/video_feed"


def build_camera_service(config: Optional[CameraConfig] = None) -> CameraService:
    """Create (but do not start) the shared ``CameraService``.

    Source selection (``CAMERA_SOURCE``):
    - ``synthetic`` device (``CAMERA_DEVICE=synthetic``): hardware-free test source.
    - ``dog``, or ``auto`` while ``ROBOT_MODE=remote``: the dog's MJPEG feed (:6500, see
      ``dog_video_url``); the laptop's own webcam is never the robot camera in remote mode.
    - ``pi``: ``CAMERA_PI_URL`` with the robot key.
    - otherwise: the local OpenCV/Yahboom source (camera wired to this machine).
    The caller owns the lifecycle: ``service.start()`` on startup, ``service.stop()`` on shutdown.
    """
    config = config or CameraConfig.from_env()
    camera_source = os.getenv("CAMERA_SOURCE", "auto").strip().lower()
    remote_robot = os.getenv("ROBOT_MODE", "").strip().lower() == "remote"
    if not config.synthetic and (camera_source == "dog" or (camera_source == "auto" and remote_robot)):
        from .remote_pi import RemotePiCameraSource
        source: FrameSource = RemotePiCameraSource(dog_video_url(), "")
        return CameraService(source, config)
    if camera_source == "pi":
        from .remote_pi import RemotePiCameraSource
        source: FrameSource = RemotePiCameraSource(
            os.getenv("CAMERA_PI_URL", "http://barry-pi.local:8100/api/v1/camera/stream"),
            os.getenv("ROBOT_API_KEY", ""),
        )
    else:
        source = SyntheticSource(config) if config.synthetic else YahboomSource(config)
    return CameraService(source, config)
