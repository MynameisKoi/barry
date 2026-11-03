"""SQLite Database Manager for BARRY.

Handles schema creation, connections, migrations, and demo data seeding.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from typing import Generator, Optional

DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).parent.parent / "barry.db"))


class DatabaseManager:
    """Manages SQLite connections and schema initialization."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.init_db()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for thread-safe SQLite connections."""
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

    def init_db(self) -> None:
        """Initializes database tables and seeds demo operators."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Operators table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS operators (
                    operator_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    tag_uid TEXT UNIQUE NOT NULL
                )
                """
            )

            # Missions table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    operator_id TEXT NOT NULL,
                    zone TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    FOREIGN KEY (operator_id) REFERENCES operators (operator_id)
                )
                """
            )

            # Events table
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

            # Seed demo operators if table is empty
            cursor.execute("SELECT COUNT(*) AS count FROM operators")
            row = cursor.fetchone()
            if row and row["count"] == 0:
                demo_operators = [
                    ("OP-001", "Alice Vance", "Chief Inspection Lead", "04:A2:3B:4C:5D:6E:7F"),
                    ("OP-002", "Bob Martinez", "Field Operations Specialist", "04:11:22:33:44:55:66"),
                    ("OP-003", "Charlie Knox", "Security & Safety Officer", "04:99:88:77:66:55:44"),
                ]
                cursor.executemany(
                    """
                    INSERT INTO operators (operator_id, name, role, tag_uid)
                    VALUES (?, ?, ?, ?)
                    """,
                    demo_operators,
                )


# Global default database instance
db = DatabaseManager()
