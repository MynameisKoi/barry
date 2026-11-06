"""Pydantic schemas and models for BARRY API."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RobotAction(str, Enum):
    FORWARD = "forward"
    BACKWARD = "backward"
    LEFT = "left"
    RIGHT = "right"
    STOP = "stop"
    EMERGENCY_STOP = "emergency_stop"


class OperatorBase(BaseModel):
    operator_id: str
    name: str
    role: str
    tag_uid: str


class OperatorResponse(OperatorBase):
    pass


class AuthScanRequest(BaseModel):
    tag_uid: str = Field(..., description="NFC tag UID (e.g. 04:A2:3B:4C:5D:6E:7F or 04A23B4C5D6E7F)")


class AuthScanResponse(BaseModel):
    authorized: bool
    operator: Optional[OperatorResponse] = None
    message: str


class RobotCommandRequest(BaseModel):
    action: str = Field(..., description="Movement command: forward, backward, left, right, stop, emergency_stop")
    speed: float = Field(default=0.2, ge=0.0, le=1.0, description="Movement speed scale (0.0 - 1.0)")


class RobotPosition(BaseModel):
    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0


class RobotStatusResponse(BaseModel):
    connected: bool
    mode: str
    # None until the hardware has reported a battery reading.
    battery_level: Optional[float] = None
    position: RobotPosition
    speed: float
    last_command: str
    emergency_stopped: bool
    timestamp: str


class MissionStartRequest(BaseModel):
    operator_id: str
    zone: str = "Zone-A"


class MissionStopRequest(BaseModel):
    reason: Optional[str] = "Normal completion"


class MissionStatusResponse(BaseModel):
    active: bool
    mission_id: Optional[str] = None
    operator_id: Optional[str] = None
    operator_name: Optional[str] = None
    zone: Optional[str] = None
    status: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class EventRecord(BaseModel):
    event_id: int
    mission_id: Optional[str] = None
    event_type: str
    description: str
    timestamp: str


class DetectionAlert(BaseModel):
    type: str = "detection"
    object: str
    confidence: float
    zone: str
    timestamp: str
    bounding_box: Optional[List[float]] = None
