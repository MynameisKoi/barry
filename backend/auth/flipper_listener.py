"""Flipper Zero USB Serial Listener for BARRY.

Monitors Flipper Zero CLI over USB serial (/dev/ttyACM*). When an NFC tag
is detected, parses the UID, verifies credentials against the database,
and notifies the Meta Quest VR headset in real-time via WebSocket.
"""

from datetime import datetime, timezone
import glob
import logging
import re
import threading
import time
from typing import Callable, Optional
import serial

logger = logging.getLogger("barry.flipper")

# Regex to match 3 to 10-byte hex UIDs/Hex IDs from Flipper NFC or 125kHz RFID CLI output
# e.g.: "Hex: 2E B7 76", "UID: 04 A2 3B 4C 5D 6E 7F", "UID: 04:A2:3B:4C", "UID: 04A23B4C5D6E7F"
UID_REGEX = re.compile(
    r"(?:UID|tag\s+uid|serial\s+number|Hex)[:\s]+([0-9A-Fa-f]{2}(?:[:\s-]?[0-9A-Fa-f]{2}){2,9})",
    re.IGNORECASE,
)


def find_flipper_port() -> Optional[str]:
    """Finds Flipper Zero USB serial device path on Linux."""
    # 1. Search /dev/serial/by-id for Flipper
    by_id_paths = glob.glob("/dev/serial/by-id/*[Ff]lipper*")
    if by_id_paths:
        return by_id_paths[0]

    # 2. Check /dev/ttyACM* ports
    acm_ports = sorted(glob.glob("/dev/ttyACM*"))
    if acm_ports:
        return acm_ports[0]

    return None


class FlipperListener:
    """Background listener for Flipper Zero serial communications."""

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 115200,
        scan_mode: str = "rfid",  # 'rfid', 'nfc', 'listen'
        on_tag_detected: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.target_port = port
        self.baudrate = baudrate
        self.scan_mode = scan_mode
        self.on_tag_detected = on_tag_detected
        self.ser: Optional[serial.Serial] = None
        self.running: bool = False
        self.thread: Optional[threading.Thread] = None
        self.last_scanned_uid: Optional[str] = None
        self.last_scan_time: float = 0.0

    def start(self) -> None:
        """Starts background monitoring thread."""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True, name="FlipperSerialThread")
        self.thread.start()
        logger.info("Flipper serial listener thread started.")

    def stop(self) -> None:
        """Stops monitoring thread and closes serial connection."""
        self.running = False
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def _run_loop(self) -> None:
        """Main connection and reading loop with auto-reconnect."""
        while self.running:
            port = self.target_port or find_flipper_port()
            if not port:
                time.sleep(2.0)
                continue

            try:
                logger.info("Attempting to connect to Flipper Zero on %s...", port)
                self.ser = serial.Serial(port, self.baudrate, timeout=1.0)
                logger.info("Connected to Flipper Zero on %s", port)

                # Send newline to wake up Flipper CLI prompt
                self.ser.write(b"\r\n")
                time.sleep(0.1)

                if self.scan_mode == "rfid":
                    logger.info("Engaging Flipper 125kHz RFID reader...")
                    self.ser.write(b"rfid read\r\n")
                elif self.scan_mode == "nfc":
                    logger.info("Engaging Flipper NFC scanner...")
                    self.ser.write(b"nfc\r\nscanner\r\n")

                while self.running and self.ser.is_open:
                    line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue

                    # Search for UID in Flipper output
                    match = UID_REGEX.search(line)
                    if match:
                        raw_uid = match.group(1).strip()
                        now = time.time()
                        # Debounce scans within 1.5 seconds
                        if raw_uid != self.last_scanned_uid or (now - self.last_scan_time) > 1.5:
                            self.last_scanned_uid = raw_uid
                            self.last_scan_time = now
                            logger.info("Flipper scanned NFC/RFID credential: %s", raw_uid)
                            if self.on_tag_detected:
                                try:
                                    self.on_tag_detected(raw_uid)
                                except Exception as err:
                                    logger.error("Callback error on tag detected: %s", err)
                            # Re-engage rfid read if in rfid mode
                            if self.scan_mode == "rfid":
                                time.sleep(0.5)
                                try:
                                    self.ser.write(b"rfid read\r\n")
                                except Exception:
                                    pass

            except serial.SerialException as se:
                logger.debug("Flipper serial disconnected (%s). Retrying...", se)
                if self.ser:
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                    self.ser = None
                time.sleep(2.0)
            except Exception as e:
                logger.error("Unexpected error in Flipper listener: %s", e)
                time.sleep(2.0)


# Global flipper listener
flipper_listener = FlipperListener()
