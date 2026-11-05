"""Mission Manager for BARRY.

Exposes MissionManager (MissionService) for mission creation, tracking,
history retrieval, and state validation.
"""

from backend.services.mission import MissionService, mission_service

MissionManager = MissionService
mission_manager = mission_service

__all__ = ["MissionManager", "mission_manager", "MissionService", "mission_service"]
