"""Event Logger and Structured Inspection Event Manager for BARRY.

Handles ingestion of DOGZILLA/simulator observations, idempotency via event_uuid,
and retrieval of inspection events alongside their AI analyses.
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional
from backend.database import DatabaseManager, db


class EventLogger:
    """Manages structured inspection events and links them to AI analyses."""

    def __init__(self, db_manager: DatabaseManager = db) -> None:
        self.db = db_manager

    def ingest_event(
        self,
        mission_id: str,
        source: str,
        event_type: str,
        data: Dict[str, Any],
        event_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Stores a structured inspection event in SQLite with idempotency protection.

        Raises ValueError if the referenced mission_id does not exist.
        """
        clean_mission = mission_id.strip()
        clean_source = source.strip()
        clean_type = event_type.strip()
        if not clean_mission or not clean_source or not clean_type:
            raise ValueError("mission_id, source, and event_type must be non-empty.")

        timestamp = datetime.now(timezone.utc).isoformat()
        data_json = json.dumps(data or {})

        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Verify mission exists
            cursor.execute(
                "SELECT mission_id, status FROM missions WHERE mission_id = ?",
                (clean_mission,),
            )
            mission_row = cursor.fetchone()
            if not mission_row:
                raise ValueError(f"Mission '{clean_mission}' not found.")

            # 2. Idempotency check if event_uuid provided
            if event_uuid:
                cursor.execute(
                    """
                    SELECT id, event_uuid, mission_id, source, event_type, event_data, timestamp
                    FROM inspection_events
                    WHERE event_uuid = ?
                    """,
                    (event_uuid.strip(),),
                )
                existing = cursor.fetchone()
                if existing:
                    return self._format_event_row(dict(existing), cursor)

            # 3. Insert into inspection_events
            cursor.execute(
                """
                INSERT INTO inspection_events (event_uuid, mission_id, source, event_type, event_data, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_uuid.strip() if event_uuid else None,
                    clean_mission,
                    clean_source,
                    clean_type,
                    data_json,
                    timestamp,
                ),
            )
            new_id = cursor.lastrowid

            # 4. Also record in general events table for unified audit history
            desc_summary = f"[{clean_source}] {clean_type}: {data_json[:180]}"
            cursor.execute(
                """
                INSERT INTO events (mission_id, event_type, description, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (clean_mission, clean_type, desc_summary, timestamp),
            )

            return {
                "id": new_id,
                "event_uuid": event_uuid.strip() if event_uuid else None,
                "mission_id": clean_mission,
                "source": clean_source,
                "event_type": clean_type,
                "data": data or {},
                "timestamp": timestamp,
                "ai_analysis": None,
            }

    def _format_event_row(
        self, row: Dict[str, Any], cursor: Any
    ) -> Dict[str, Any]:
        """Formats an inspection_events row and attaches its latest AI analysis if present."""
        try:
            parsed_data = json.loads(row["event_data"]) if row.get("event_data") else {}
        except Exception:
            parsed_data = {"raw": row.get("event_data")}

        cursor.execute(
            """
            SELECT id, mission_id, event_id, provider, model, risk_level, analysis_json, created_at, status, latency_ms
            FROM ai_analyses
            WHERE event_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (row["id"],),
        )
        ai_row = cursor.fetchone()
        ai_record = None
        if ai_row:
            ai_dict = dict(ai_row)
            try:
                analysis_obj = json.loads(ai_dict["analysis_json"])
            except Exception:
                analysis_obj = None
            ai_record = {
                "id": ai_dict["id"],
                "mission_id": ai_dict["mission_id"],
                "event_id": ai_dict["event_id"],
                "provider": ai_dict["provider"],
                "model": ai_dict["model"],
                "risk_level": ai_dict["risk_level"],
                "analysis": analysis_obj,
                "created_at": ai_dict["created_at"],
                "status": ai_dict["status"],
                "latency_ms": ai_dict["latency_ms"],
            }

        return {
            "id": row["id"],
            "event_uuid": row.get("event_uuid"),
            "mission_id": row["mission_id"],
            "source": row["source"],
            "event_type": row["event_type"],
            "data": parsed_data,
            "timestamp": row["timestamp"],
            "ai_analysis": ai_record,
        }

    def get_inspection_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves a single structured inspection event by ID, including AI analysis."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, event_uuid, mission_id, source, event_type, event_data, timestamp
                FROM inspection_events
                WHERE id = ?
                """,
                (event_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._format_event_row(dict(row), cursor)

    def list_inspection_events(
        self, mission_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Lists structured inspection events, optionally filtered by mission_id."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if mission_id:
                cursor.execute(
                    """
                    SELECT id, event_uuid, mission_id, source, event_type, event_data, timestamp
                    FROM inspection_events
                    WHERE mission_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (mission_id.strip(), limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, event_uuid, mission_id, source, event_type, event_data, timestamp
                    FROM inspection_events
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
            return [self._format_event_row(dict(r), cursor) for r in rows]


event_logger = EventLogger()
