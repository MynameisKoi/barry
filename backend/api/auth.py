from typing import List
from fastapi import APIRouter, HTTPException, Query, status

from backend.auth.authorization import auth_service
from backend.models import AuthScanRequest, AuthScanResponse, OperatorResponse

router = APIRouter(tags=["Authentication"])


@router.get("/api/auth/logs")
async def get_authorization_logs(limit: int = Query(50, ge=1, le=500)):
    """Returns audit log of NFC/RFID credential scan attempts."""
    return auth_service.get_authorization_logs(limit=limit)
