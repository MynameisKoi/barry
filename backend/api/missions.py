from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, Query, status

from backend.services.mission import mission_service

router = APIRouter(tags=["Missions"])


@router.get("/api/missions")
async def list_missions(limit: int = Query(50, ge=1, le=500)) -> List[Dict[str, Any]]:
    """Returns a list of recent and historical missions."""
    return mission_service.list_missions(limit=limit)


@router.get("/api/missions/{mission_id}")
async def get_mission_details(mission_id: str) -> Dict[str, Any]:
    """Returns details for a specific mission by ID."""
    mission = mission_service.get_mission_by_id(mission_id)
    if not mission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mission '{mission_id}' not found.",
        )
    return mission
