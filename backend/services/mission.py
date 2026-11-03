"""Mission Management Service for BARRY.

Handles mission lifecycle (start, track, stop), audit event persistence,
and active inspection state.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid
from backend.database import DatabaseManager, db


class MissionService:
    """Coordinates inspection missions and audit log events."""

    def __init__(self, db_manager: DatabaseManager = db) -> None:
        self.db = db_manager
        self._active_mission_id: Optional[str] = None

    def start_mission(self, operator_id: str, zone: str = "Zone-A") -> Dict[str, Any]:
        """Starts a new inspection mission under an authorized operator."""
        timestamp = datetime.now(timezone.utc).isoformat()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            # Verify operator exists
            cursor.execute(
                "SELECT operator_id, name, role FROM operators WHERE operator_id = ?",
                (operator_id.strip(),),
            )
            op = cursor.fetchone()
            if not op:
                raise ValueError(f"Cannot start mission: Operator '{operator_id}' not found.")

            # Check if there is already an active mission
            cursor.execute(
                "SELECT mission_id FROM missions WHERE status = 'in_progress' ORDER BY started_at DESC LIMIT 1"
            )
            active_row = cursor.fetchone()
            if active_row:
                self._active_mission_id = active_row["mission_id"]
                raise RuntimeError(
                    f"Mission '{self._active_mission_id}' is already in progress. End it before starting a new one."
                )

            # Create new mission
            mission_id = f"MSN-{uuid.uuid4().hex[:8].upper()}"
            cursor.execute(
                """
                INSERT INTO missions (mission_id, operator_id, zone, status, started_at, ended_at)
                VALUES (?, ?, ?, 'in_progress', ?, NULL)
                """,
                (mission_id, operator_id.strip(), zone.strip(), timestamp),
            )

            # Record event
            cursor.execute(
                """
                INSERT INTO events (mission_id, event_type, description, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (
                    mission_id,
                    "mission_start",
                    f"Mission {mission_id} started in zone '{zone}' by {op['name']} ({op['role']}).",
                    timestamp,
                ),
            )

            self._active_mission_id = mission_id
            return {
                "active": True,
                "mission_id": mission_id,
                "operator_id": op["operator_id"],
                "operator_name": op["name"],
                "zone": zone.strip(),
                "status": "in_progress",
                "started_at": timestamp,
                "ended_at": None,
            }

    def stop_mission(self, reason: str = "Normal completion") -> Dict[str, Any]:
        """Terminates the active inspection mission."""
        timestamp = datetime.now(timezone.utc).isoformat()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT m.mission_id, m.operator_id, o.name AS operator_name, m.zone, m.started_at
                FROM missions m
                LEFT JOIN operators o ON m.operator_id = o.operator_id
                WHERE m.status = 'in_progress'
                ORDER BY m.started_at DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                self._active_mission_id = None
                return {
                    "active": False,
                    "message": "No active mission currently in progress.",
                }

            mission_id = row["mission_id"]
            cursor.execute(
                """
                UPDATE missions
                SET status = 'completed', ended_at = ?
                WHERE mission_id = ?
                """,
                (timestamp, mission_id),
            )

            cursor.execute(
                """
                INSERT INTO events (mission_id, event_type, description, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (
                    mission_id,
                    "mission_stop",
                    f"Mission {mission_id} ended. Reason: {reason}.",
                    timestamp,
                ),
            )

            self._active_mission_id = None
            return {
                "active": False,
                "mission_id": mission_id,
                "operator_id": row["operator_id"],
                "operator_name": row["operator_name"],
                "zone": row["zone"],
                "status": "completed",
                "started_at": row["started_at"],
                "ended_at": timestamp,
                "message": f"Mission {mission_id} completed successfully.",
            }

    def get_status(self) -> Dict[str, Any]:
        """Fetches status of the currently active mission, or reports inactive."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT m.mission_id, m.operator_id, o.name AS operator_name, m.zone, m.status, m.started_at, m.ended_at
                FROM missions m
                LEFT JOIN operators o ON m.operator_id = o.operator_id
                WHERE m.status = 'in_progress'
                ORDER BY m.started_at DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row:
                self._active_mission_id = row["mission_id"]
                return {
                    "active": True,
                    "mission_id": row["mission_id"],
                    "operator_id": row["operator_id"],
                    "operator_name": row["operator_name"],
                    "zone": row["zone"],
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                }

            # If no active mission, return the most recent mission summary
            cursor.execute(
                """
                SELECT m.mission_id, m.operator_id, o.name AS operator_name, m.zone, m.status, m.started_at, m.ended_at
                FROM missions m
                LEFT JOIN operators o ON m.operator_id = o.operator_id
                ORDER BY m.started_at DESC LIMIT 1
                """
            )
            last_row = cursor.fetchone()
            self._active_mission_id = None
            if last_row:
                return {
                    "active": False,
                    "mission_id": last_row["mission_id"],
                    "operator_id": last_row["operator_id"],
                    "operator_name": last_row["operator_name"],
                    "zone": last_row["zone"],
                    "status": last_row["status"],
                    "started_at": last_row["started_at"],
                    "ended_at": last_row["ended_at"],
                }

            return {
                "active": False,
                "mission_id": None,
                "operator_id": None,
                "operator_name": None,
                "zone": None,
                "status": "idle",
                "started_at": None,
                "ended_at": None,
            }

    def log_event(self, event_type: str, description: str, mission_id: Optional[str] = None) -> int:
        """Appends an event to the audit trail."""
        timestamp = datetime.now(timezone.utc).isoformat()
        target_mission = mission_id or self._active_mission_id

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO events (mission_id, event_type, description, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (target_mission, event_type, description, timestamp),
            )
            return cursor.lastrowid

    def get_events(self, mission_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves audit trail events."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if mission_id:
                cursor.execute(
                    """
                    SELECT event_id, mission_id, event_type, description, timestamp
                    FROM events
                    WHERE mission_id = ?
                    ORDER BY event_id DESC LIMIT ?
                    """,
                    (mission_id, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT event_id, mission_id, event_type, description, timestamp
                    FROM events
                    ORDER BY event_id DESC LIMIT ?
                    """,
                    (limit,),
                )
            return [dict(row) for row in cursor.fetchall()]


# Global default mission service
mission_service = MissionService()
