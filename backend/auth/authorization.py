"""NFC Authorization Module for BARRY.

Handles NFC test credential verification, normalization, operator lookup,
and authentication event logging.
"""

from datetime import datetime, timezone
import re
from typing import List, Optional, Tuple
from backend.database import DatabaseManager, db


def normalize_uid(raw_uid: str) -> str:
    """Normalizes an NFC UID string to uppercase colon-delimited format.

    Supports inputs like:
      - 04:A2:3B:4C:5D:6E:7F
      - 04-a2-3b-4c-5d-6e-7f
      - 04 a2 3b 4c 5d 6e 7f
      - 04A23B4C5D6E7F
    """
    clean_hex = re.sub(r"[^0-9A-Fa-f]", "", raw_uid).upper()
    if not clean_hex:
        return raw_uid.strip().upper()
    # Format as pairs separated by colons if length is even
    if len(clean_hex) % 2 == 0 and len(clean_hex) >= 8:
        return ":".join(clean_hex[i : i + 2] for i in range(0, len(clean_hex), 2))
    return clean_hex


class AuthService:
    """Manages NFC credential verification and operator profiles."""

    def __init__(self, db_manager: DatabaseManager = db) -> None:
        self.db = db_manager

    def verify_tag_uid(self, raw_uid: str) -> Tuple[bool, Optional[dict], str]:
        """Verifies an NFC tag UID against authorized operators.

        Returns (is_authorized, operator_dict_or_None, message).
        """
        if not raw_uid or not raw_uid.strip():
            return False, None, "Tag UID cannot be empty."

        normalized = normalize_uid(raw_uid)
        timestamp = datetime.now(timezone.utc).isoformat()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Allow matching either exact normalized or raw uppercase
            cursor.execute(
                """
                SELECT operator_id, name, role, tag_uid
                FROM operators
                WHERE UPPER(tag_uid) = ? OR UPPER(tag_uid) = ?
                """,
                (normalized, raw_uid.strip().upper()),
            )
            row = cursor.fetchone()

            if row:
                operator = dict(row)
                # Log successful auth event
                cursor.execute(
                    """
                    INSERT INTO events (mission_id, event_type, description, timestamp)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        None,
                        "auth_success",
                        f"NFC credential verified for {operator['name']} ({operator['role']}) [UID: {normalized}]",
                        timestamp,
                    ),
                )
                return True, operator, f"Authorized: {operator['name']} ({operator['role']})"
            else:
                # Log failed auth event
                cursor.execute(
                    """
                    INSERT INTO events (mission_id, event_type, description, timestamp)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        None,
                        "auth_failed",
                        f"Unauthorized NFC scan attempt with UID: {normalized}",
                        timestamp,
                    ),
                )
                return False, None, f"Unauthorized credential: UID {normalized} is not registered."

    def list_operators(self) -> List[dict]:
        """Returns all registered operators."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT operator_id, name, role, tag_uid FROM operators ORDER BY operator_id ASC")
            return [dict(row) for row in cursor.fetchall()]

    def register_operator(self, operator_id: str, name: str, role: str, tag_uid: str) -> dict:
        """Registers a new test operator credential."""
        normalized = normalize_uid(tag_uid)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO operators (operator_id, name, role, tag_uid)
                VALUES (?, ?, ?, ?)
                """,
                (operator_id.strip(), name.strip(), role.strip(), normalized),
            )
        return {
            "operator_id": operator_id.strip(),
            "name": name.strip(),
            "role": role.strip(),
            "tag_uid": normalized,
        }


# Global default auth service
auth_service = AuthService()
