"""Synthetic raw-frame source for development without camera hardware."""

from __future__ import annotations

import cv2
import numpy as np

from .config import CameraConfig
from .interface import CameraUnavailableError, FrameCaptureError
from .source import FrameSource


class SyntheticSource(FrameSource):
    """Generate a lightweight animated BGR inspection frame in memory."""

    def __init__(self, config: CameraConfig) -> None:
        self._config = config
        self._opened = False
        self._sequence = 0

    @property
    def name(self) -> str:
        """Return the synthetic source's stable diagnostic name."""
        return "synthetic"

    def open(self) -> None:
        """Enable generation when ``CAMERA_DEVICE=synthetic``."""
        if not self._config.synthetic:
            raise CameraUnavailableError(
                "SyntheticSource requires camera device 'synthetic'"
            )
        self._opened = True

    def read(self) -> np.ndarray:
        """Return the next generated raw BGR frame."""
        if not self._opened:
            raise FrameCaptureError("synthetic source is not open")

        self._sequence += 1
        width = self._config.width
        height = self._config.height
        frame = np.full((height, width, 3), (26, 22, 18), dtype=np.uint8)

        grid_step = max(20, min(width, height) // 12)
        for x in range(0, width, grid_step):
            cv2.line(frame, (x, 0), (x, height), (42, 35, 28), 1)
        for y in range(0, height, grid_step):
            cv2.line(frame, (0, y), (width, y), (42, 35, 28), 1)

        center = (width // 2, height // 2)
        radius = max(5, min(width, height) // 10)
        cv2.circle(frame, center, radius, (0, 200, 255), 1)
        cv2.circle(frame, center, max(2, radius // 12), (0, 255, 128), -1)

        travel = max(1, width - 20)
        marker_x = 10 + (self._sequence * 7) % travel
        cv2.line(frame, (marker_x, 0), (marker_x, height - 1), (255, 140, 0), 2)
        cv2.putText(
            frame,
            f"BARRY SYNTHETIC // FRAME {self._sequence:06d}",
            (10, min(30, height - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 200),
            1,
            cv2.LINE_AA,
        )
        return frame

    def close(self) -> None:
        """Disable generation; repeated calls are safe."""
        self._opened = False

