"""Camera configuration (camera_plan.md section 8).

All settings are read from ``CAMERA_*`` environment variables, validated, and
exposed as one immutable ``CameraConfig``. Nothing is hardcoded in other
modules, and no credentials live here (auth keys belong to the router, #14).
"""

from __future__ import annotations

import os
from typing import Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class CameraConfigError(ValueError):
    """Raised when camera environment settings are missing or invalid."""


class CameraConfig(BaseModel):
    """Validated, immutable camera settings."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    # Device index (``0``) or path (``/dev/video0``). ``synthetic`` selects the
    # built-in test-frame source for development without hardware.
    device: Union[int, str] = 0
    width: int = Field(640, ge=160, le=3840)
    height: int = Field(480, ge=120, le=2160)
    target_fps: float = Field(20, gt=0, le=60)
    jpeg_quality: int = Field(75, ge=1, le=100)
    max_viewers: int = Field(3, ge=1, le=32)
    stall_timeout_s: float = Field(2.0, gt=0, le=60)

    @field_validator("device", mode="before")
    @classmethod
    def _parse_device(cls, value):
        """Numeric strings become int indexes; anything else stays a path/name."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("device must not be empty")
            if value.lstrip("-").isdigit():
                value = int(value)
        if isinstance(value, int) and value < 0:
            raise ValueError("device index must be >= 0")
        return value

    @property
    def synthetic(self) -> bool:
        """True when the development test-frame source should be used."""
        return self.device == "synthetic"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "CameraConfig":
        """Build a config from ``CAMERA_*`` variables (defaults if unset).

        Variables: CAMERA_ENABLED, CAMERA_DEVICE, CAMERA_WIDTH, CAMERA_HEIGHT,
        CAMERA_TARGET_FPS, CAMERA_JPEG_QUALITY, CAMERA_MAX_VIEWERS,
        CAMERA_STALL_TIMEOUT_S.

        Raises:
            CameraConfigError: if any value fails validation.
        """
        env = os.environ if env is None else env
        names = {
            "enabled": "CAMERA_ENABLED",
            "device": "CAMERA_DEVICE",
            "width": "CAMERA_WIDTH",
            "height": "CAMERA_HEIGHT",
            "target_fps": "CAMERA_TARGET_FPS",
            "jpeg_quality": "CAMERA_JPEG_QUALITY",
            "max_viewers": "CAMERA_MAX_VIEWERS",
            "stall_timeout_s": "CAMERA_STALL_TIMEOUT_S",
        }
        raw = {f: env[v] for f, v in names.items() if env.get(v, "").strip() != ""}
        if "enabled" in raw:
            text = raw["enabled"].strip().lower()
            if text not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
                raise CameraConfigError(f"CAMERA_ENABLED: invalid boolean {raw['enabled']!r}")
            raw["enabled"] = text in {"1", "true", "yes", "on"}
        try:
            return cls(**raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{names.get(str(e['loc'][0]), e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise CameraConfigError(problems) from exc
