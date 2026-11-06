"""Browser-compatible authentication for the Camera API (camera_plan.md 11).

A browser ``<img>`` element cannot attach custom headers, so the shared secret
is accepted from any of (checked in this order):

1. ``X-Camera-Key`` request header  (laptop relay, scripts, curl)
2. ``?token=...`` query parameter   (``<img src=".../stream?token=...">``)
3. ``camera_token`` cookie          (browser session)

The secret comes from the ``CAMERA_API_KEY`` environment variable and is never
stored in source. Comparison is constant-time. Fail-closed: when no key is
configured every request is refused (503), so the camera can never be exposed
unauthenticated by forgetting a setting.

Note: a query token can appear in access logs and browser history; prefer the
header or cookie when the client can set them, and use HTTPS or a trusted LAN.
"""

from __future__ import annotations

import hmac
import os
from typing import Mapping, Optional

from fastapi import HTTPException, Request, status

API_KEY_ENV = "CAMERA_API_KEY"
HEADER_NAME = "X-Camera-Key"
QUERY_PARAM = "token"
COOKIE_NAME = "camera_token"


class CameraAuth:
    """Callable FastAPI dependency that validates the camera access token."""

    def __init__(self, api_key: Optional[str]) -> None:
        # Empty/whitespace-only keys count as "not configured".
        self._key = api_key.strip().encode("utf-8") if api_key and api_key.strip() else None

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "CameraAuth":
        """Read ``CAMERA_API_KEY`` from the environment (or the given mapping)."""
        env = os.environ if env is None else env
        return cls(env.get(API_KEY_ENV))

    @property
    def configured(self) -> bool:
        """True when a non-empty key is set."""
        return self._key is not None

    def verify(self, candidate: Optional[str]) -> bool:
        """Constant-time check of a presented token. False if none configured."""
        if self._key is None or not candidate:
            return False
        return hmac.compare_digest(candidate.encode("utf-8"), self._key)

    def __call__(self, request: Request) -> None:
        """Dependency: raise 503 if unconfigured, 401 if no valid token."""
        if not self.configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="camera authentication is not configured",
            )
        presented = (
            request.headers.get(HEADER_NAME)
            or request.query_params.get(QUERY_PARAM)
            or request.cookies.get(COOKIE_NAME)
        )
        if not self.verify(presented):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing camera token",
                headers={"WWW-Authenticate": "CameraKey"},
            )
