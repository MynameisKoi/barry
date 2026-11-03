#!/usr/bin/env python3
"""Interactive test script to verify Flipper Zero 125kHz RFID reading."""

import sys
import time
from pathlib import Path
import serial

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.auth.authorization import auth_service
from backend.auth.flipper_listener import UID_REGEX, find_flipper_port

port = find_flipper_port() or "/dev/ttyACM0"
print(f"\n[1] Connecting to Flipper Zero on {port}...")

try:
    ser = serial.Serial(port, 115200, timeout=1.0)
except Exception as e:
    print(f"[ERROR] Could not open port {port}: {e}")
    sys.exit(1)

# Reset CLI prompt
ser.write(b"\x03\r\n\x03\r\n")
time.sleep(0.3)
ser.read(2048)

print("[2] Engaging 125kHz RFID reader ('rfid read')...")
ser.write(b"rfid read\r\n")
time.sleep(0.3)

initial = ser.read(1024).decode("utf-8", errors="ignore").strip()
print(f"[3] Flipper status: {initial}")
print("\n" + "=" * 60)
print(">>> TAP YOUR HID H10301 CARD TO THE BACK OF THE FLIPPER ZERO NOW <<<")
print("=" * 60 + "\n")

start_time = time.time()
found = False

try:
    while time.time() - start_time < 30:
        line = ser.readline().decode("utf-8", errors="ignore").strip()
        if line:
            print(f"[FLIPPER RAW]: {line}")
            match = UID_REGEX.search(line)
            if match:
                raw_uid = (match.group(1) or match.group(2)).strip()
                print(f"\n🎯 [DETECTED CREDENTIAL]: {raw_uid}")
                auth, op, msg = auth_service.verify_tag_uid(raw_uid)
                if auth:
                    print(f"✅ [SUCCESS - AUTHORIZED]: {msg} (Operator: {op['operator_id']})\n")
                else:
                    print(f"❌ [UNAUTHORIZED]: {msg}\n")
                found = True
                break
        time.sleep(0.05)

    if not found:
        print("\n[TIMEOUT] No card detected within 30 seconds.")

except KeyboardInterrupt:
    print("\nAborted by user.")
finally:
    ser.write(b"\x03\r\n")
    time.sleep(0.2)
    ser.close()
    print("[4] Connection closed safely.\n")
