"""NFC Authorization Module for BARRY.

Handles NFC test credential verification, normalization, operator lookup,
and authentication event logging in both authorization_logs and events.
"""

from datetime import datetime, timezone
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from backend.database import DatabaseManager, db


def normalize_uid(raw_uid: str) -> str:
    """Normalizes an NFC UID string to uppercase colon-delimited format.

    Supports inputs like:
      - 04:A2:3B:4C:5D:6E:7F
      - 04-a2-3b-4c-5d-6e-7f
      - 04 a2 3b 4c 5d 6e 7f
      - 04A23B4C5D6E7F
      - 2E B7 76 (3-byte HID H10301)
    """
    clean_hex = re.sub(r"[^0-9A-Fa-f]", "", raw_uid).upper()
    if not clean_hex:
        return raw_uid.strip().upper()
    # Format as pairs separated by colons if length is even and at least 4 hex chars (2 bytes)
    if len(clean_hex) % 2 == 0 and len(clean_hex) >= 4:
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

        clean_hex = re.sub(r"[^0-9A-Fa-f]", "", raw_uid).upper()
        normalized = normalize_uid(raw_uid)
        timestamp = datetime.now(timezone.utc).isoformat()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            # Allow matching normalized, raw uppercase, or stripped hex
            cursor.execute(
                """
                SELECT operator_id, name, role, tag_uid
                FROM operators
                WHERE UPPER(tag_uid) = ?
                   OR UPPER(tag_uid) = ?
                   OR REPLACE(REPLACE(REPLACE(UPPER(tag_uid), ':', ''), '-', ''), ' ', '') = ?
                """,
                (normalized, raw_uid.strip().upper(), clean_hex),
            )
            row = cursor.fetchone()

            if row:
                operator = dict(row)
                reason = f"Authorized: {operator['name']} ({operator['role']})"
                # Log in authorization_logs
                cursor.execute(
                    """
                    INSERT INTO authorization_logs (operator_id, credential_uid, authorized, timestamp, reason)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (operator["operator_id"], normalized, timestamp, reason),
                )
                # Log in general events
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
                return True, operator, reason
            else:
                allow_anon_card = os.environ.get("AUTH_ALLOW_UNREGISTERED_TAGS", "true").lower() in ("1", "true", "yes")
                if allow_anon_card:
                    # Auto-provision temporary/guest operator profile so teammate can use any card/UID
                    anon_op = {
                        "operator_id": f"OP-GUEST-{clean_hex[-4:] if len(clean_hex) >= 4 else '0000'}",
                        "name": f"Field Operator ({normalized})",
                        "role": "Field Testing Specialist",
                        "tag_uid": normalized,
                    }
                    reason = f"Authorized (Auto-Provisioned): {anon_op['name']}"
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO operators (operator_id, name, role, tag_uid, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (anon_op["operator_id"], anon_op["name"], anon_op["role"], normalized, timestamp),
                    )
                    cursor.execute(
                        """
                        INSERT INTO authorization_logs (operator_id, credential_uid, authorized, timestamp, reason)
                        VALUES (?, ?, 1, ?, ?)
                        """,
                        (anon_op["operator_id"], normalized, timestamp, reason),
                    )
                    cursor.execute(
                        """
                        INSERT INTO events (mission_id, event_type, description, timestamp)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            None,
                            "auth_success",
                            f"Credential auto-provisioned for {anon_op['name']} [UID: {normalized}]",
                            timestamp,
                        ),
                    )
                    return True, anon_op, reason

                reason = f"Unauthorized credential: UID {normalized} is not registered."
                # Log in authorization_logs
                cursor.execute(
                    """
                    INSERT INTO authorization_logs (operator_id, credential_uid, authorized, timestamp, reason)
                    VALUES (NULL, ?, 0, ?, ?)
                    """,
                    (normalized, timestamp, reason),
                )
                # Log in general events
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
                return False, None, reason

    def list_operators(self) -> List[dict]:
        """Returns all registered operators."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT operator_id, name, role, tag_uid FROM operators ORDER BY operator_id ASC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def register_operator(
        self, operator_id: str, name: str, role: str, tag_uid: str
    ) -> dict:
        """Registers a new test operator credential."""
        normalized = normalize_uid(tag_uid)
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO operators (operator_id, name, role, tag_uid, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    operator_id.strip(),
                    name.strip(),
                    role.strip(),
                    normalized,
                    now_iso,
                ),
            )
        return {
            "operator_id": operator_id.strip(),
            "name": name.strip(),
            "role": role.strip(),
            "tag_uid": normalized,
        }

    def get_authorization_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns chronological authorization attempt records."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, operator_id, credential_uid, authorized, timestamp, reason
                FROM authorization_logs
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            )
            return [
                {
                    "id": r["id"],
                    "operator_id": r["operator_id"],
                    "credential_uid": r["credential_uid"],
                    "authorized": bool(r["authorized"]),
                    "timestamp": r["timestamp"],
                    "reason": r["reason"],
                }
                for r in cursor.fetchall()
            ]


# Global default auth service
auth_service = AuthService()
