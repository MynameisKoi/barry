"""Tests for NFC authorization module and API endpoints."""

import pytest
from fastapi.testclient import TestClient
from backend.auth.authorization import AuthService, normalize_uid
from backend.database import DatabaseManager
from backend.main import app


@pytest.fixture
def test_db(tmp_path):
    """Creates an isolated temporary database for test execution."""
    db_file = str(tmp_path / "test_auth.db")
    mgr = DatabaseManager(db_path=db_file)
    return mgr


def test_normalize_uid():
    """Verifies that NFC and RFID UIDs are normalized consistently."""
    assert normalize_uid("04:a2:3b:4c:5d:6e:7f") == "04:A2:3B:4C:5D:6E:7F"
    assert normalize_uid("04-A2-3B-4C-5D-6E-7F") == "04:A2:3B:4C:5D:6E:7F"
    assert normalize_uid("04 A2 3B 4C 5D 6E 7F") == "04:A2:3B:4C:5D:6E:7F"
    assert normalize_uid("04a23b4c5d6e7f") == "04:A2:3B:4C:5D:6E:7F"
    # 3-byte HID H10301 RFID card
    assert normalize_uid("2E B7 76") == "2E:B7:76"
    assert normalize_uid("2eb776") == "2E:B7:76"
    assert normalize_uid("2E:B7:76") == "2E:B7:76"


def test_auth_service_hid_card(test_db):
    """Verifies that 3-byte HID H10301 cards can be registered and verified in any format."""
    service = AuthService(db_manager=test_db)
    service.register_operator("OP-004", "Test Lead", "Commander", "2E:B7:76")

    # Match via spaced format (as displayed on Flipper screen)
    auth1, op1, _ = service.verify_tag_uid("2E B7 76")
    assert auth1 is True
    assert op1["operator_id"] == "OP-004"

    # Match via raw hex
    auth2, op2, _ = service.verify_tag_uid("2EB776")
    assert auth2 is True
    assert op2["operator_id"] == "OP-004"

    # Match via normalized
    auth3, op3, _ = service.verify_tag_uid("2E:B7:76")
    assert auth3 is True
    assert op3["operator_id"] == "OP-004"


def test_auth_service_authorized(test_db):
    """Verifies authorized operator test credential lookup."""
    service = AuthService(db_manager=test_db)
    authorized, operator, msg = service.verify_tag_uid("04:A2:3B:4C:5D:6E:7F")
    assert authorized is True
    assert operator is not None
    assert operator["operator_id"] == "OP-001"
    assert "Alice Vance" in msg


def test_auth_service_unauthorized(test_db):
    """Verifies unauthorized test credential rejection."""
    service = AuthService(db_manager=test_db)
    authorized, operator, msg = service.verify_tag_uid("DE:AD:BE:EF:00:11:22")
    assert authorized is False
    assert operator is None
    assert "Unauthorized" in msg


def test_auth_service_empty_uid(test_db):
    """Verifies empty UID handling."""
    service = AuthService(db_manager=test_db)
    authorized, operator, msg = service.verify_tag_uid("   ")
    assert authorized is False
    assert operator is None


def test_auth_api_endpoints():
    """Tests FastAPI /api/auth/scan and /api/operators endpoints."""
    with TestClient(app) as client:
        # 1. Test listing operators
        resp = client.get("/api/operators")
        assert resp.status_code == 200
        operators = resp.json()
        assert len(operators) >= 3

        # 2. Test valid scan
        scan_resp = client.post("/api/auth/scan", json={"tag_uid": "04:A2:3B:4C:5D:6E:7F"})
        assert scan_resp.status_code == 200
        data = scan_resp.json()
        assert data["authorized"] is True
        assert data["operator"]["operator_id"] == "OP-001"

        # 3. Test invalid scan
        bad_resp = client.post("/api/auth/scan", json={"tag_uid": "FF:EE:DD:CC:BB:AA"})
        assert bad_resp.status_code == 401
        bad_data = bad_resp.json()["detail"]
        assert bad_data["authorized"] is False
