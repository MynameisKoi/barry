#!/usr/bin/env python3
"""Interactive CLI to register physical NFC test cards/tags into BARRY."""

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.auth.authorization import auth_service, normalize_uid


def main():
    parser = argparse.ArgumentParser(description="Register a physical NFC test card/tag into BARRY.")
    parser.add_argument("--id", dest="operator_id", help="Operator ID (e.g. OP-004)")
    parser.add_argument("--name", dest="name", help="Operator full name")
    parser.add_argument("--role", dest="role", help="Operator role / title")
    parser.add_argument("--uid", dest="tag_uid", help="NFC Tag UID (e.g. 04:A2:3B:4C:5D:6E:7F or 04A23B...)")
    parser.add_argument("--list", action="store_true", help="List all registered test credentials")

    args = parser.parse_args()

    if args.list:
        operators = auth_service.list_operators()
        print("\n=== REGISTERED OPERATORS & CREDENTIALS ===")
        print(f"{'OPERATOR ID':<15} {'NAME':<25} {'ROLE':<30} {'TAG UID'}")
        print("-" * 90)
        for op in operators:
            print(f"{op['operator_id']:<15} {op['name']:<25} {op['role']:<30} {op['tag_uid']}")
        print(f"\nTotal: {len(operators)} operators\n")
        return

    # If flags not provided, prompt interactively
    operator_id = args.operator_id or input("Enter Operator ID (e.g. OP-004): ").strip()
    name = args.name or input("Enter Operator Name (e.g. Dana Scully): ").strip()
    role = args.role or input("Enter Operator Role (e.g. Field Inspector): ").strip()
    raw_uid = args.tag_uid or input("Enter NFC Tag UID from Flipper Zero: ").strip()

    if not operator_id or not name or not role or not raw_uid:
        print("[ERROR] All fields are required to register an operator.", file=sys.stderr)
        sys.exit(1)

    normalized = normalize_uid(raw_uid)
    try:
        res = auth_service.register_operator(operator_id, name, role, normalized)
        print("\n✅ Operator credential successfully enrolled!")
        print(f"   ID:       {res['operator_id']}")
        print(f"   Name:     {res['name']}")
        print(f"   Role:     {res['role']}")
        print(f"   Tag UID:  {res['tag_uid']}\n")
    except Exception as e:
        print(f"\n[ERROR] Failed to register operator: {e}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
