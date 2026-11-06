"""MJPEG wire-format adapter for :mod:`backend.camera.service`."""

from __future__ import annotations

from typing import Iterator

from .interface import CameraInterface


def mjpeg_generator(service: CameraInterface) -> Iterator[bytes]:
    """Convert service frames to ``multipart/x-mixed-replace`` chunks."""
    for frame in service.stream():
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            + f"Content-Length: {len(frame.jpeg)}\r\n\r\n".encode("ascii")
            + frame.jpeg
            + b"\r\n"
        )
