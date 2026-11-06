"""Camera status model.

Structured, JSON-serialisable description of the camera service state
(camera_plan.md section 6). Configured settings (``target_fps``) are kept
separate from measured values (``actual_fps``, ``last_frame_age_ms``); a
measurement that is unavailable is ``None`` and is never fabricated.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Resolution(BaseModel):
    """Frame dimensions in pixels."""

    model_config = ConfigDict(frozen=True)

    width: int = Field(ge=1)
    height: int = Field(ge=1)


class CameraStatus(BaseModel):
    """Operational state of the camera service (response of ``GET /status``)."""

    connected: bool = Field(False, description="Camera device is accessible")
    capturing: bool = Field(False, description="Capture loop is active")
    streaming: bool = Field(False, description="At least one viewer is being served")
    resolution: Optional[Resolution] = Field(
        None, description="Current frame size; None if unknown"
    )
    target_fps: float = Field(0, ge=0, description="Configured capture rate")
    actual_fps: Optional[float] = Field(
        None, ge=0, description="Measured rate; None if not measured yet"
    )
    active_viewers: int = Field(0, ge=0, description="Current stream subscribers")
    last_frame_age_ms: Optional[float] = Field(
        None, ge=0, description="Ms since last successful capture; None if no frame yet"
    )

    @classmethod
    def disconnected(cls, target_fps: float = 0) -> "CameraStatus":
        """Useful default status for a camera that is off or unreachable."""
        return cls(target_fps=target_fps)
