from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Query, status

from backend.ai.schemas import InspectionEventIngest, InspectionEventResponse

router = APIRouter(tags=["Audit & Events"])
