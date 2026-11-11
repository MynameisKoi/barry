"""Browser-compatible authentication for the Camera API (camera_plan.md 11).

A browser ``<img>`` element cannot attach custom headers, so the shared secret
is accepted from either of:

1. configured request header (default ``X-Camera-Key``; Pi API uses ``X-Robot-Key``)
2. ``?token=...`` query parameter   (``<img src=".../stream?token=...">``)

The request is accepted if ANY presented credential verifies, so a stale value
in one place never blocks a valid one in the other. No cookie/session is used.

The secret comes from the ``CAMERA_API_KEY`` environment variable and is never
stored in source. Comparison is constant-time. Fail-closed: when no key is
configured every request is refused (503), so the camera can never be exposed
unauthenticated by forgetting a setting.

Note: a query token can appear in access logs and browser history; prefer the
header when the client can set it, and use HTTPS or a trusted LAN.
"""

from __future__ import annotations

import hmac
import os
from typing import Mapping, Optional

from fastapi import HTTPException, Request, status

API_KEY_ENV = "CAMERA_API_KEY"
HEADER_NAME = "X-Camera-Key"
QUERY_PARAM = "token"


class CameraAuth:
    """Callable FastAPI dependency that validates the camera access token."""

    def __init__(self, api_key: Optional[str], *, allow_anonymous: bool = False,
                 header_name: str = HEADER_NAME) -> None:
        # Empty/whitespace-only keys count as "not configured".
        self._key = api_key.strip().encode("utf-8") if api_key and api_key.strip() else None
        self._allow_anonymous = allow_anonymous
        self._header_name = header_name

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "CameraAuth":
        """Read ``CAMERA_API_KEY`` from the environment (or the given mapping)."""
        env = os.environ if env is None else env
        allow_anon = env.get("CAMERA_API_KEY_ALLOW_ANONYMOUS", "").lower() in ("1", "true", "yes")
        return cls(env.get(API_KEY_ENV), allow_anonymous=allow_anon)

    @property
    def configured(self) -> bool:
        """True when a non-empty key is set."""
        return self._key is not None or self._allow_anonymous

    def verify(self, candidate: Optional[str]) -> bool:
        """Constant-time check of a presented token. False if none configured."""
        if self._key is None or not candidate:
            return False
        return hmac.compare_digest(candidate.encode("utf-8"), self._key)

    def __call__(self, request: Request) -> None:
        """Dependency: raise 503 if unconfigured, 401 if no valid token."""
        if self._allow_anonymous:
            return
        if not self.configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="camera authentication is not configured",
            )
        # Any presented credential may satisfy auth; verify every one (no
        # short-circuit on the first non-empty value, so a stale header cannot
        # mask a valid ?token=).
        presented = (
            request.headers.get(self._header_name),
            *request.query_params.getlist(QUERY_PARAM),
        )
        if not any([self.verify(c) for c in presented]):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing camera token",
                headers={"WWW-Authenticate": "CameraKey"},
            )
