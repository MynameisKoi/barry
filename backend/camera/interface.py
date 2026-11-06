"""Camera interface (camera_plan.md section 9).

Abstract contract that every camera implementation honours (Yahboom adapter,
synthetic test source, capture service). Callers - the stream generator and
the FastAPI router - depend only on this module, never on OpenCV or hardware.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

from .status import CameraStatus


class CameraError(Exception):
    """Base class for all camera failures."""


class CameraUnavailableError(CameraError):
    """Camera not found, failed to initialise, or disconnected.
    The router maps this to HTTP 503."""


class FrameCaptureError(CameraError):
    """A frame could not be read or JPEG-encoded."""


class ViewerLimitError(CameraError):
    """``CAMERA_MAX_VIEWERS`` reached; the router maps this to HTTP 429/503."""


@dataclass(frozen=True)
class Frame:
    """One encoded camera frame."""

    jpeg: bytes  # JPEG image bytes
    timestamp: float  # time.monotonic() at capture
    width: int
    height: int
    sequence: int  # increases by 1 per captured frame


class CameraInterface(ABC):
    """Capture, snapshot and status operations for a single camera."""

    @abstractmethod
    def start(self) -> None:
        """Open the device, validate a frame, start capture.

        Idempotent. Raises CameraUnavailableError after bounded retries fail.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop capture, close streams and release the device. Idempotent, never raises."""

    @abstractmethod
    def snapshot(self) -> bytes:
        """Return the latest *fresh* valid JPEG (never a stale frame).

        Raises CameraUnavailableError if no valid recent frame exists.
        """

    @abstractmethod
    def latest_frame(self) -> Frame:
        """Return the latest frame object. Same errors as ``snapshot``."""

    @abstractmethod
    def stream(self) -> Iterator[Frame]:
        """Yield the newest frame to one viewer; slow viewers skip frames (no queue).

        Registers a viewer on first iteration and always unregisters it when
        the iterator closes (client disconnect or shutdown).
        Raises ViewerLimitError / CameraUnavailableError before yielding.
        """

    @abstractmethod
    def status(self) -> CameraStatus:
        """Current status. Never raises; works while disconnected."""
