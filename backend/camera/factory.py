"""Wiring helper that assembles the camera stack from configuration."""

from __future__ import annotations

from typing import Optional
import os

from .config import CameraConfig
from .service import CameraService
from .source import FrameSource
from .synthetic import SyntheticSource
from .yahboom import YahboomSource


def build_camera_service(config: Optional[CameraConfig] = None) -> CameraService:
    """Create (but do not start) the shared ``CameraService``.

    ``CAMERA_DEVICE=synthetic`` selects the hardware-free test source;
    anything else selects the OpenCV/Yahboom source. The caller owns the
    lifecycle: call ``service.start()`` on app startup and ``service.stop()``
    on shutdown.
    """
    config = config or CameraConfig.from_env()
    camera_source = os.getenv("CAMERA_SOURCE", "auto").strip().lower()
    if camera_source == "pi":
        from .remote_pi import RemotePiCameraSource
        source: FrameSource = RemotePiCameraSource(
            os.getenv("CAMERA_PI_URL", "http://barry-pi.local:8100/api/v1/camera/stream"),
            os.getenv("ROBOT_API_KEY", ""),
        )
    else:
        source = SyntheticSource(config) if config.synthetic else YahboomSource(config)
    return CameraService(source, config)
