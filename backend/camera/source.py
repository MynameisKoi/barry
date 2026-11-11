"""Raw camera frame source contract.

Frame sources own only device access and return OpenCV-compatible BGR arrays.
Encoding, retries, pacing, and viewer management belong to ``CameraService``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class FrameSource(ABC):
    """Abstract source of raw BGR camera frames."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name for status and diagnostics."""

    @abstractmethod
    def open(self) -> None:
        """Open and validate the source.

        Raises:
            CameraUnavailableError: If the source cannot be opened.
        """

    @abstractmethod
    def read(self) -> np.ndarray:
        """Return one raw BGR frame.

        Raises:
            FrameCaptureError: If a valid frame cannot be captured.
        """

    @abstractmethod
    def close(self) -> None:
        """Release source resources. Implementations must be idempotent."""


class CameraServiceSource(FrameSource):
    """Adapts a running CameraService as a FrameSource without duplicate connections."""

    def __init__(self, service) -> None:
        self._service = service

    @property
    def name(self) -> str:
        source = getattr(self._service, "_source", None)
        return f"service:{getattr(source, 'name', 'camera')}"

    def open(self) -> None:
        pass

    def read(self) -> np.ndarray:
        raw = self._service.latest_raw_frame()
        if raw is None:
            from .interface import FrameCaptureError
            raise FrameCaptureError("no camera frame available from camera service")
        return raw

    def close(self) -> None:
        pass
