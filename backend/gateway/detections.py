"""Thin gateway reader for the single background YOLO producer."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

AVAILABLE, UNAVAILABLE, NO_CAMERA, NO_MODEL = "available", "unavailable", "no_camera", "no_model"


class LiveDetections:
    """Read latest DetectionLoop output; this class never decodes or infers."""

    def __init__(self, service: Callable[[], Any] | Any) -> None:
        self._service = service

    def _get_service(self):
        return self._service() if callable(self._service) else self._service

    def state(self) -> str:
        try:
            service = self._get_service()
            return service.status() if service is not None else UNAVAILABLE
        except Exception:
            logger.exception("Could not read YOLO loop status")
            return UNAVAILABLE

    async def detections(self) -> list[dict[str, Any]]:
        try:
            service = self._get_service()
            if service is None:
                return []
            return await asyncio.to_thread(service.detections)
        except Exception:
            logger.exception("Could not read YOLO loop results")
            return []
