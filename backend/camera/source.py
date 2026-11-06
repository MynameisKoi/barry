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
