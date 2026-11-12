"""The ``look()`` tool: the newest shared-camera frame, downscaled, as an image for Claude.

* Reads the one shared ``CameraService`` (``latest_frame()``); it never opens a second device handle.
* A frame older than ``MAX_FRAME_AGE_S`` (2 s), no camera, or an unreadable frame raises
  :class:`CameraLost`; the task runner then stops the robot and ends the run ("camera lost").
* The JPEG is downscaled to <= 640 px wide and re-encoded at quality 70 so each image costs few tokens
  and a run's append-only history stays small.
* :func:`image_block` builds the Messages API image block (``{"type": "image", "source": {"type":
  "base64", "media_type": "image/jpeg", "data": ...}}``). That is the shape the installed ``anthropic``
  SDK types accept inside a ``tool_result``'s ``content`` list (``BetaToolResultBlockParam.content``).
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Any, Callable

MAX_LOOK_WIDTH = 640
JPEG_QUALITY = 70
MAX_FRAME_AGE_S = 2.0


class CameraLost(RuntimeError):
    """No usable camera frame: missing camera, unreadable frame, or a frame older than 2 s."""


@dataclass(frozen=True)
class LookImage:
    jpeg: bytes
    width: int
    height: int
    frame_age_s: float

    def block(self) -> dict[str, Any]:
        return image_block(self.jpeg)


def image_block(jpeg: bytes) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(jpeg).decode("ascii")}}


def downscale_jpeg(jpeg: bytes) -> tuple[bytes, int, int]:
    """Decode, shrink to <= MAX_LOOK_WIDTH wide (aspect kept) and re-encode. Runs in a worker thread."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise CameraLost("camera frame could not be decoded")
    height, width = image.shape[:2]
    if width > MAX_LOOK_WIDTH:
        scale = MAX_LOOK_WIDTH / width
        image = cv2.resize(image, (MAX_LOOK_WIDTH, max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
        height, width = image.shape[:2]
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise CameraLost("camera frame could not be encoded")
    return encoded.tobytes(), width, height


async def capture_look(camera: Callable[[], Any] | Any, *, clock: Callable[[], float] = time.monotonic) -> LookImage:
    """Newest frame -> downscaled JPEG. Raises CameraLost when there is nothing fresh to look at."""
    service = camera() if callable(camera) else camera
    if service is None:
        raise CameraLost("no camera")
    try:
        frame = service.latest_frame()
    except Exception as exc:  # CameraUnavailableError or any camera fault
        raise CameraLost("camera has no fresh frame") from exc
    age = clock() - frame.timestamp
    if age > MAX_FRAME_AGE_S:
        raise CameraLost(f"camera frame is {age:.1f} s old")
    jpeg, width, height = await asyncio.to_thread(downscale_jpeg, frame.jpeg)
    return LookImage(jpeg, width, height, round(max(age, 0.0), 2))
