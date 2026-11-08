"""Transparent laptop relay for the Quest-facing Robot API v1.

The Quest connects only to the laptop.  This service forwards its Robot API
requests to the Pi, injecting the laptop-to-Pi key while preserving control
session and command sequencing headers.  STOP uses a dedicated HTTP client so
it can never wait behind a slow motion request or an exhausted motion pool.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from backend.robot import protocol
from backend.robot.config import RobotConfig

logger = logging.getLogger(__name__)

_FORWARDED_COMMAND_HEADERS: Iterable[str] = (
    protocol.SESSION_HEADER,
    protocol.SEQ_HEADER,
    protocol.SENT_AT_HEADER,
)
_FORWARDED_RESPONSE_HEADERS: Iterable[str] = (
    "content-type",
    protocol.SESSION_HEADER,
    protocol.SEQ_HEADER,
)


class RobotApiRelay:
    """Async raw HTTP relay with a priority lane for STOP."""

    def __init__(
        self,
        config: Optional[RobotConfig] = None,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        stop_transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.config = config or RobotConfig.from_env()
        self._motion_timeout_s = min(self.config.remote_timeout_s, 0.5)
        default_timeout = httpx.Timeout(self.config.remote_timeout_s)
        priority_timeout = httpx.Timeout(self._motion_timeout_s)
        common_headers = (
            {protocol.AUTH_HEADER: self.config.api_key}
            if self.config.api_key
            else {}
        )
        self._client = httpx.AsyncClient(
            base_url=self.config.remote_url,
            headers=common_headers,
            timeout=default_timeout,
            transport=transport,
        )
        # Separate pool/transport: a hung motion can neither hold a lock nor
        # consume the connection needed by a safety-critical STOP.
        self._stop_client = httpx.AsyncClient(
            base_url=self.config.remote_url,
            headers=common_headers,
            timeout=priority_timeout,
            transport=stop_transport if stop_transport is not None else transport,
        )

    async def forward(
        self,
        request: Request,
        path: str,
        *,
        priority_stop: bool = False,
    ) -> Response:
        headers = self._request_headers(request)
        body = await request.body()
        client = self._stop_client if priority_stop else self._client
        timeout_s = (
            self._motion_timeout_s
            if path == protocol.PATH_MOTION or priority_stop
            else self.config.remote_timeout_s
        )
        try:
            upstream = await client.request(
                request.method,
                path,
                content=body if body else None,
                headers=headers,
                timeout=httpx.Timeout(timeout_s),
            )
        except httpx.RequestError as exc:
            logger.warning("Pi Robot API unavailable for %s %s: %s", request.method, path, exc)
            return JSONResponse(
                status_code=503,
                content={"detail": "Pi Robot API unavailable."},
            )

        response_headers = {
            name: upstream.headers[name]
            for name in _FORWARDED_RESPONSE_HEADERS
            if name in upstream.headers
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._stop_client.aclose()

    def _request_headers(self, request: Request) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.config.api_key:
            # Never trust/pass the headset's key as the Pi credential.  The
            # laptop owns the shared laptop-to-Pi secret.
            headers[protocol.AUTH_HEADER] = self.config.api_key
        for name in _FORWARDED_COMMAND_HEADERS:
            if name in request.headers:
                headers[name] = request.headers[name]
        if "content-type" in request.headers:
            headers["content-type"] = request.headers["content-type"]
        return headers
