#!/usr/bin/env python3
"""Standalone Flipper Zero USB serial monitor for BARRY.

Listens to Flipper Zero over USB-C, extracts scanned NFC tag UIDs,
and triggers BARRY mission authorization via HTTP REST API or database.
"""

import argparse
import sys
import time
from pathlib import Path
import httpx

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.auth.flipper_listener import FlipperListener, find_flipper_port


def on_tag_scanned(raw_uid: str, api_url: str):
    print(f"\n[FLIPPER] >>> Detected NFC Tag: {raw_uid}")
    endpoint = f"{api_url.rstrip('/')}/api/auth/scan"
    print(f"[FLIPPER] Posting to {endpoint}...")
    try:
        resp = httpx.post(endpoint, json={"tag_uid": raw_uid}, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            op = data.get("operator", {})
            print(f"✅ [AUTHORIZED] {op.get('name')} ({op.get('role')}) - Operator ID: {op.get('operator_id')}")
        else:
            print(f"❌ [UNAUTHORIZED] Credential rejected by BARRY backend.")
    except Exception as e:
        print(f"[WARNING] Backend not reachable at {endpoint} ({e}).")


def main():
    parser = argparse.ArgumentParser(description="Monitor Flipper Zero NFC scans over USB.")
    parser.add_argument("--port", help="Serial port (e.g. /dev/ttyACM0). Auto-detected if omitted.")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--api", default="http://localhost:8000", help="BARRY backend base URL")

    args = parser.parse_args()

    port = args.port or find_flipper_port()
    if not port:
        print("[INFO] No Flipper Zero USB device currently found in /dev/ttyACM* or /dev/serial/by-id/.")
        print("[INFO] Please connect your Flipper Zero via USB-C and ensure it is powered on.")
        print("[INFO] Waiting for Flipper Zero connection...")

    def callback(uid: str):
        on_tag_scanned(uid, args.api)

    listener = FlipperListener(port=args.port, baudrate=args.baud, on_tag_detected=callback)
    listener.start()

    print("[BARRY FLIPPER MONITOR] Running... Press Ctrl+C to terminate.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping Flipper monitor...")
        listener.stop()


if __name__ == "__main__":
    main()
