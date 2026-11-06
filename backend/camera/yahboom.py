"""OpenCV-backed frame source for the Yahboom DOGZILLA camera."""

from __future__ import annotations

from typing import Callable, Optional, Protocol, Union

import cv2
import numpy as np

from .config import CameraConfig
from .interface import CameraUnavailableError, FrameCaptureError
from .source import FrameSource


class VideoCaptureLike(Protocol):
    """Small subset of ``cv2.VideoCapture`` used by ``YahboomSource``."""

    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, Optional[np.ndarray]]: ...

    def release(self) -> None: ...

    def set(self, prop_id: int, value: float) -> bool: ...


CaptureFactory = Callable[[Union[int, str]], VideoCaptureLike]


class YahboomSource(FrameSource):
    """Read raw BGR frames from a camera through ``cv2.VideoCapture``.

    The factory is injectable so tests and nonstandard Yahboom deployments do
    not need real camera hardware. The first frame is captured during
    :meth:`open` to validate the device, then returned by the first
    :meth:`read` call rather than being discarded.
    """

    def __init__(
        self,
        config: CameraConfig,
        capture_factory: CaptureFactory = cv2.VideoCapture,
    ) -> None:
        self._config = config
        self._capture_factory = capture_factory
        self._capture: Optional[VideoCaptureLike] = None
        self._first_frame: Optional[np.ndarray] = None

    @property
    def name(self) -> str:
        """Return the adapter's stable diagnostic name."""
        return "yahboom"

    def open(self) -> None:
        """Open and configure the device, then validate its first frame."""
        if self._capture is not None:
            return

        capture: Optional[VideoCaptureLike] = None
        try:
            capture = self._capture_factory(self._config.device)
            if not capture.isOpened():
                raise CameraUnavailableError(
                    f"camera device {self._config.device!r} could not be opened"
                )

            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._config.width))
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._config.height))
            capture.set(cv2.CAP_PROP_FPS, float(self._config.target_fps))

            ok, frame = capture.read()
            if not ok or not self._valid_frame(frame):
                raise CameraUnavailableError(
                    f"camera device {self._config.device!r} returned no valid frame"
                )

            self._capture = capture
            self._first_frame = frame
        except CameraUnavailableError:
            self._release(capture)
            raise
        except Exception as exc:
            self._release(capture)
            raise CameraUnavailableError(
                f"camera device {self._config.device!r} failed to initialise"
            ) from exc

    def read(self) -> np.ndarray:
        """Return the next valid raw BGR frame."""
        capture = self._capture
        if capture is None:
            raise FrameCaptureError("camera source is not open")

        if self._first_frame is not None:
            frame = self._first_frame
            self._first_frame = None
            return frame

        try:
            ok, frame = capture.read()
        except Exception as exc:
            raise FrameCaptureError("camera frame capture failed") from exc
        if not ok or not self._valid_frame(frame):
            raise FrameCaptureError("camera returned no valid BGR frame")
        return frame

    def close(self) -> None:
        """Release the camera device; repeated calls are safe."""
        capture = self._capture
        self._capture = None
        self._first_frame = None
        self._release(capture)

    @staticmethod
    def _valid_frame(frame: Optional[np.ndarray]) -> bool:
        return (
            isinstance(frame, np.ndarray)
            and frame.size > 0
            and frame.ndim == 3
            and frame.shape[2] == 3
        )

    @staticmethod
    def _release(capture: Optional[VideoCaptureLike]) -> None:
        if capture is not None:
            try:
                capture.release()
            except Exception:
                # Cleanup must stay safe and idempotent, including faulty SDKs.
                pass

