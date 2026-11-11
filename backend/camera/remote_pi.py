"""FrameSource adapter for the authenticated Pi camera MJPEG stream."""

from __future__ import annotations

import cv2
import httpx
import numpy as np

from .interface import CameraUnavailableError, FrameCaptureError
from .source import FrameSource


class RemotePiCameraSource(FrameSource):
    def __init__(self, url: str, api_key: str, *, transport: httpx.BaseTransport | None = None,
                 timeout_s: float = 10.0) -> None:
        self.url, self.api_key = url, api_key
        self.transport, self.timeout_s = transport, timeout_s
        self._client: httpx.Client | None = None
        self._response: httpx.Response | None = None
        self._chunks = None
        self._buffer = bytearray()

    @property
    def name(self) -> str:
        return "remote-pi"

    def open(self) -> None:
        self.close()
        self._client = httpx.Client(transport=self.transport, timeout=self.timeout_s)
        try:
            request = self._client.build_request("GET", self.url, headers={"X-Robot-Key": self.api_key})
            self._response = self._client.send(request, stream=True)
            self._response.raise_for_status()
            self._chunks = self._response.iter_bytes()
        except Exception as exc:
            self.close()
            raise CameraUnavailableError("Pi camera stream is unavailable") from exc

    def read(self) -> np.ndarray:
        if self._chunks is None:
            raise FrameCaptureError("Pi camera source is not open")
        while True:
            start = self._buffer.find(b"\xff\xd8")
            if start >= 0:
                end = self._buffer.find(b"\xff\xd9", start + 2)
                if end >= 0:
                    jpeg = bytes(self._buffer[start:end + 2])
                    del self._buffer[:end + 2]
                    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if image is None:
                        raise FrameCaptureError("Pi camera returned an invalid JPEG frame")
                    return image
                if start:
                    del self._buffer[:start]
            try:
                self._buffer.extend(next(self._chunks))
            except StopIteration as exc:
                raise FrameCaptureError("Pi camera stream ended") from exc
            except Exception as exc:
                raise FrameCaptureError("Pi camera stream read failed") from exc
            if len(self._buffer) > 8 * 1024 * 1024:
                self._buffer.clear()
                raise FrameCaptureError("Pi camera stream contained no bounded JPEG frame")

    def close(self) -> None:
        if self._response is not None:
            self._response.close()
            self._response = None
        if self._client is not None:
            self._client.close()
            self._client = None
        self._chunks = None
        self._buffer.clear()
