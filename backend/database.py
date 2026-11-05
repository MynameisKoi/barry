"""SQLite Database Manager for BARRY.

Handles schema creation, connections, safe migrations, and demo data seeding
for operators, missions, inspection events, AI analyses, and authorization logs.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Generator

DEFAULT_DB_PATH = os.getenv(
    "DATABASE_PATH", str(Path(__file__).resolve().parent.parent / "barry.db")
)


class DatabaseManager:
    """Manages SQLite connections, schema initialization, and migrations."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.init_db()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for thread-safe SQLite connections with FK enforcement."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_column(
        self, cursor: sqlite3.Cursor, table: str, column: str, col_def: str
    ) -> None:
        """Safely adds a column to an existing table if it does not exist."""
        cursor.execute(f"PRAGMA table_info({table})")
        existing_cols = {row["name"] for row in cursor.fetchall()}
        if column not in existing_cols:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")

    def init_db(self) -> None:
        """Initializes all database tables, indexes, and seeds default operators."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Operators table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS operators (
                    operator_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    tag_uid TEXT UNIQUE NOT NULL,
                    created_at TEXT
                )
                """
            )
            self._ensure_column(cursor, "operators", "created_at", "TEXT")

            # 2. Missions table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    operator_id TEXT NOT NULL,
                    zone TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    created_at TEXT,
                    FOREIGN KEY (operator_id) REFERENCES operators (operator_id)
                )
                """
            )
            self._ensure_column(cursor, "missions", "created_at", "TEXT")

            # 3. General System Events table (Audit & Legacy Compatibility)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT,
                    event_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (mission_id) REFERENCES missions (mission_id)
                )
                """
            )

            # 4. Structured Inspection Events table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS inspection_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_uuid TEXT UNIQUE,
                    mission_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (mission_id) REFERENCES missions (mission_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_inspection_events_mission ON inspection_events(mission_id);"
            )

            # 5. AI Analyses table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT,
                    event_id INTEGER,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms REAL,
                    FOREIGN KEY (mission_id) REFERENCES missions (mission_id),
                    FOREIGN KEY (event_id) REFERENCES inspection_events (id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_analyses_mission ON ai_analyses(mission_id);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_analyses_event ON ai_analyses(event_id);"
            )

            # 6. Authorization Logs table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS authorization_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operator_id TEXT,
                    credential_uid TEXT NOT NULL,
                    authorized INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    FOREIGN KEY (operator_id) REFERENCES operators (operator_id)
                )
                """
            )

            # Seed demo operators if table is empty
            cursor.execute("SELECT COUNT(*) AS count FROM operators")
            row = cursor.fetchone()
            if row and row["count"] == 0:
                demo_operators = [
                    (
                        "OP-001",
                        "Alice Vance",
                        "Chief Inspection Lead",
                        "04:A2:3B:4C:5D:6E:7F",
                        now_iso,
                    ),
                    (
                        "OP-002",
                        "Bob Martinez",
                        "Field Operations Specialist",
                        "04:11:22:33:44:55:66",
                        now_iso,
                    ),
                    (
                        "OP-003",
                        "Charlie Knox",
                        "Security & Safety Officer",
                        "04:99:88:77:66:55:44",
                        now_iso,
                    ),
                ]
                cursor.executemany(
                    """
                    INSERT INTO operators (operator_id, name, role, tag_uid, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    demo_operators,
                )


# Global default database instance
db = DatabaseManager()
