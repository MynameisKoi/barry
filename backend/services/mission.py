"""Mission Management Service for BARRY.

Handles mission lifecycle (start, track, stop, cancel), mission history retrieval,
audit event persistence, and active inspection state.
"""

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional
import uuid
from backend.database import DatabaseManager, db


class MissionService:
    """Coordinates inspection missions and audit log events."""

    def __init__(self, db_manager: DatabaseManager = db) -> None:
        self.db = db_manager
        self._active_mission_id: Optional[str] = None

    def start_mission(
        self, operator_id: str, zone: str = "Zone-A", mission_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Starts a new inspection mission under an authorized operator."""
        timestamp = datetime.now(timezone.utc).isoformat()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            # Verify operator exists, or auto-provision if unregistered
            cursor.execute(
                "SELECT operator_id, name, role FROM operators WHERE operator_id = ?",
                (operator_id.strip(),),
            )
            op = cursor.fetchone()
            if not op:
                allow_anon_op = os.environ.get("AUTH_ALLOW_UNREGISTERED_TAGS", "true").lower() in ("1", "true", "yes")
                if allow_anon_op:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO operators (operator_id, name, role, tag_uid, created_at)
                        VALUES (?, ?, 'Field Operator', ?, ?)
                        """,
                        (operator_id.strip(), f"Operator {operator_id.strip()}", f"AUTOGEN-{operator_id.strip()}", timestamp),
                    )
                    op = {"operator_id": operator_id.strip(), "name": f"Operator {operator_id.strip()}", "role": "Field Operator"}
                else:
                    raise ValueError(
                        f"Cannot start mission: Operator '{operator_id}' not found."
                    )

            # Check if there is already an active mission
            cursor.execute(
                "SELECT mission_id FROM missions WHERE status IN ('in_progress', 'active') ORDER BY started_at DESC LIMIT 1"
            )
            active_row = cursor.fetchone()
            if active_row:
                self._active_mission_id = active_row["mission_id"]
                raise RuntimeError(
                    f"Mission '{self._active_mission_id}' is already in progress. End it before starting a new one."
                )

            # Create new mission
            new_mission_id = (
                mission_id.strip()
                if mission_id
                else f"MSN-{uuid.uuid4().hex[:8].upper()}"
            )
            cursor.execute(
                """
                INSERT INTO missions (mission_id, operator_id, zone, status, started_at, ended_at, created_at)
                VALUES (?, ?, ?, 'in_progress', ?, NULL, ?)
                """,
                (new_mission_id, operator_id.strip(), zone.strip(), timestamp, timestamp),
            )

            # Record event
            cursor.execute(
                """
                INSERT INTO events (mission_id, event_type, description, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (
                    new_mission_id,
                    "mission_start",
                    f"Mission {new_mission_id} started in zone '{zone}' by {op['name']} ({op['role']}).",
                    timestamp,
                ),
            )

            self._active_mission_id = new_mission_id
            return {
                "active": True,
                "mission_id": new_mission_id,
                "operator_id": op["operator_id"],
                "operator_name": op["name"],
                "zone": zone.strip(),
                "status": "in_progress",
                "started_at": timestamp,
                "ended_at": None,
            }

    def stop_mission(
        self, reason: str = "Normal completion", final_status: str = "completed"
    ) -> Dict[str, Any]:
        """Terminates the active inspection mission."""
        timestamp = datetime.now(timezone.utc).isoformat()
        target_status = (
            final_status if final_status in ("completed", "cancelled") else "completed"
        )

        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT m.mission_id, m.operator_id, o.name AS operator_name, m.zone, m.started_at
                FROM missions m
                LEFT JOIN operators o ON m.operator_id = o.operator_id
                WHERE m.status IN ('in_progress', 'active')
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
                SET status = ?, ended_at = ?
                WHERE mission_id = ?
                """,
                (target_status, timestamp, mission_id),
            )

            cursor.execute(
                """
                INSERT INTO events (mission_id, event_type, description, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (
                    mission_id,
                    "mission_stop",
                    f"Mission {mission_id} ended ({target_status}). Reason: {reason}.",
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
                "status": target_status,
                "started_at": row["started_at"],
                "ended_at": timestamp,
                "message": f"Mission {mission_id} {target_status} successfully.",
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
                WHERE m.status IN ('in_progress', 'active')
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

    def list_missions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves historical list of missions."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT m.mission_id, m.operator_id, o.name AS operator_name, o.role AS operator_role,
                       m.zone, m.status, m.started_at, m.ended_at
                FROM missions m
                LEFT JOIN operators o ON m.operator_id = o.operator_id
                ORDER BY m.started_at DESC LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "active": r["status"] in ("in_progress", "active"),
                    "mission_id": r["mission_id"],
                    "operator_id": r["operator_id"],
                    "operator_name": r["operator_name"],
                    "operator_role": r["operator_role"],
                    "zone": r["zone"],
                    "status": r["status"],
                    "started_at": r["started_at"],
                    "ended_at": r["ended_at"],
                }
                for r in rows
            ]

    def get_mission_by_id(self, mission_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves complete mission details including events and AI analyses."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT m.mission_id, m.operator_id, o.name AS operator_name, o.role AS operator_role,
                       m.zone, m.status, m.started_at, m.ended_at
                FROM missions m
                LEFT JOIN operators o ON m.operator_id = o.operator_id
                WHERE m.mission_id = ?
                """,
                (mission_id.strip(),),
            )
            row = cursor.fetchone()
            if not row:
                return None

            # Fetch structured inspection events
            cursor.execute(
                """
                SELECT id, event_uuid, mission_id, source, event_type, event_data, timestamp
                FROM inspection_events
                WHERE mission_id = ?
                ORDER BY id ASC
                """,
                (mission_id.strip(),),
            )
            insp_events = []
            for ie in cursor.fetchall():
                ie_dict = dict(ie)
                try:
                    ie_dict["data"] = json.loads(ie_dict.pop("event_data"))
                except Exception:
                    ie_dict["data"] = {}
                insp_events.append(ie_dict)

            # Fetch AI analyses for this mission
            cursor.execute(
                """
                SELECT id, mission_id, event_id, provider, model, risk_level, analysis_json, created_at, status, latency_ms
                FROM ai_analyses
                WHERE mission_id = ?
                ORDER BY id ASC
                """,
                (mission_id.strip(),),
            )
            analyses = []
            for an in cursor.fetchall():
                an_dict = dict(an)
                try:
                    an_dict["analysis"] = json.loads(an_dict.pop("analysis_json"))
                except Exception:
                    an_dict["analysis"] = None
                analyses.append(an_dict)

            # Fetch general audit events
            cursor.execute(
                """
                SELECT event_id, mission_id, event_type, description, timestamp
                FROM events
                WHERE mission_id = ?
                ORDER BY event_id ASC
                """,
                (mission_id.strip(),),
            )
            audit_events = [dict(e) for e in cursor.fetchall()]

            return {
                "active": row["status"] in ("in_progress", "active"),
                "mission_id": row["mission_id"],
                "operator_id": row["operator_id"],
                "operator_name": row["operator_name"],
                "operator_role": row["operator_role"],
                "zone": row["zone"],
                "status": row["status"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "inspection_events": insp_events,
                "ai_analyses": analyses,
                "audit_events": audit_events,
            }

    def log_event(
        self, event_type: str, description: str, mission_id: Optional[str] = None,
        source: Optional[str] = None, command_id: Optional[str] = None,
        result_code: Optional[int] = None, latency_ms: Optional[float] = None,
    ) -> int:
        """Appends an event to the audit trail."""
        timestamp = datetime.now(timezone.utc).isoformat()
        target_mission = mission_id or self._active_mission_id

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO events (mission_id, event_type, description, timestamp,
                    source, command_id, result_code, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (target_mission, event_type, description, timestamp, source,
                 command_id, result_code, latency_ms),
            )
            return cursor.lastrowid

    def get_events(
        self, mission_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Retrieves audit trail events."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if mission_id:
                cursor.execute(
                    """
                    SELECT event_id, mission_id, event_type, description, timestamp,
                        source, command_id, result_code, latency_ms
                    FROM events
                    WHERE mission_id = ?
                    ORDER BY event_id DESC LIMIT ?
                    """,
                    (mission_id, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT event_id, mission_id, event_type, description, timestamp,
                        source, command_id, result_code, latency_ms
                    FROM events
                    ORDER BY event_id DESC LIMIT ?
                    """,
                    (limit,),
                )
            return [dict(row) for row in cursor.fetchall()]


# Global default mission service
mission_service = MissionService()
