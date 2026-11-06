"""FastAPI router for the Camera API (camera_plan.md sections 4, 5, 6, 12).

Routes are thin: they authenticate, delegate to an injected
:class:`CameraInterface`, and translate camera errors to HTTP status codes.
No camera object lives at module level; the app supplies it via
``create_camera_router``.
"""

from __future__ import annotations

from contextlib import suppress
from typing import AsyncIterator, Callable, Iterator

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse

from .auth import CameraAuth
from .interface import (
    CameraError,
    CameraInterface,
    CameraUnavailableError,
    ViewerLimitError,
)
from .status import CameraStatus
from .stream import mjpeg_generator

NO_STORE = {"Cache-Control": "no-store"}
MJPEG_MEDIA_TYPE = "multipart/x-mixed-replace; boundary=frame"


class _ViewerStreamingResponse(StreamingResponse):
    """StreamingResponse that always releases its viewer slot.

    The slot is taken when the frame iterator is first advanced (before the
    200 is sent, so 429/503 can still be returned). Cleanup therefore must not
    depend on the body generator having started or finished: ``__call__``
    closes both the body generator and the frame iterator on every exit path
    (normal end, client disconnect, send error, cancellation).
    """

    def __init__(self, body: AsyncIterator[bytes], chunks: Iterator[bytes], **kwargs):
        super().__init__(body, **kwargs)
        self._chunks = chunks

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with suppress(Exception):
                await self.body_iterator.aclose()
            # ValueError only if a worker thread is still inside next();
            # anyio waits for it on cancellation, so this is a safe no-op guard.
            with suppress(ValueError):
                self._chunks.close()


def create_camera_router(
    get_camera: Callable[[], CameraInterface], auth: CameraAuth
) -> APIRouter:
    """Build the camera router.

    Args:
        get_camera: Zero-arg provider of the shared camera (e.g. returns
            ``app.state.camera``). Called per request, so it is overridable.
        auth: Dependency that enforces the access token on every route.
    """
    router = APIRouter(
        prefix="/api/v1/camera", tags=["camera"], dependencies=[Depends(auth)]
    )

    def camera_dep() -> CameraInterface:
        return get_camera()

    def to_http(exc: CameraError) -> HTTPException:
        """Map camera errors to HTTP errors (503 unavailable, 429 viewer cap)."""
        if isinstance(exc, ViewerLimitError):
            return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc), NO_STORE)
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc), NO_STORE)

    @router.get("/status", response_model=CameraStatus)
    def get_status(
        response: Response, camera: CameraInterface = Depends(camera_dep)
    ) -> CameraStatus:
        """Camera health. Always 200: a disconnected camera is a valid status."""
        response.headers.update(NO_STORE)
        return camera.status()

    @router.get(
        "/snapshot",
        responses={200: {"content": {"image/jpeg": {}}}},
        response_class=Response,
    )
    def get_snapshot(camera: CameraInterface = Depends(camera_dep)) -> Response:
        """Latest fresh JPEG; 503 if no valid recent frame exists."""
        try:
            jpeg = camera.snapshot()
        except CameraError as exc:
            raise to_http(exc) from exc
        return Response(content=jpeg, media_type="image/jpeg", headers=NO_STORE)

    @router.get(
        "/stream",
        responses={200: {"content": {MJPEG_MEDIA_TYPE: {}}}},
        response_class=StreamingResponse,
    )
    async def get_stream(
        camera: CameraInterface = Depends(camera_dep),
    ) -> StreamingResponse:
        """MJPEG stream. Errors (503/429) are raised before the 200 is sent."""
        chunks = mjpeg_generator(camera)
        try:
            # Pull the first chunk now: viewer-limit and availability errors
            # surface here, while we can still return a proper HTTP error.
            first = await to_thread.run_sync(next, chunks, None)
        except CameraError as exc:
            raise to_http(exc) from exc
        if first is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "camera stream ended", NO_STORE
            )

        async def body() -> AsyncIterator[bytes]:
            # The blocking generator is advanced in a worker thread. On client
            # disconnect the await below is cancelled only after the in-flight
            # ``next`` finishes (anyio default), so ``close`` in ``finally`` is
            # safe and always unregisters the viewer.
            try:
                yield first
                while True:
                    chunk = await to_thread.run_sync(next, chunks, None)
                    if chunk is None:
                        return
                    yield chunk
            finally:
                chunks.close()

        return _ViewerStreamingResponse(
            body(), chunks, media_type=MJPEG_MEDIA_TYPE, headers=NO_STORE
        )

    return router
