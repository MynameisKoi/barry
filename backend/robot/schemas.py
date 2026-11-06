"""Side-effect-free request schemas shared by robot API entry points."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PostureUpdateRequest(BaseModel):
    """Request schema for updating DOGZILLA posture and movement style."""

    height_z: Optional[float] = Field(default=None, ge=75.0, le=115.0)
    roll: Optional[float] = Field(default=None, ge=-20.0, le=20.0)
    pitch: Optional[float] = Field(default=None, ge=-15.0, le=15.0)
    yaw: Optional[float] = Field(default=None, ge=-11.0, le=11.0)
    gait_type: Optional[str] = Field(default=None, description="trot, walk, or high_walk")
    pace: Optional[str] = Field(default=None, description="slow, normal, or high")
    imu_enabled: Optional[bool] = None
    preset: Optional[str] = Field(
        default=None, description="stand, crouch, or obstacle_crossing"
    )
