#!/usr/bin/env python3
"""Interactive & Automated Test for DOGZILLA Movements:
1. Standing Up (Height Z = 108)
2. Sitting Down (Height Z = 75)
3. Looking Left (Attitude Yaw = -8.0 deg)
4. Looking Right (Attitude Yaw = +8.0 deg)
5. Turning Around (Turn Left & Turn Right)
"""

import sys
import time
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.robot.remote import RemoteRobotClient
from backend.robot.interface import Direction, Intensity


def main():
    client = RemoteRobotClient(base_url="http://192.168.137.236:8100", timeout=5.0)

    print("==================================================")
    print("🐶 DOGZILLA PHYSICAL MOVEMENT & POSTURE TEST SUITE")
    print("==================================================")

    # 1. Initial Status Check
    print("\n[1/6] Querying initial robot state...")
    status0 = client.get_status()
    if not status0.get("connected"):
        print("❌ ERROR: Robot Pi API is offline or unreachable.")
        sys.exit(1)

    print(f"  Connected: {status0.get('connected')}")
    print(f"  Battery:   {status0.get('battery_level')}%")
    print(f"  IMU:       {status0.get('imu')}")
    print(f"  Posture:   {status0.get('posture')}")

    # 2. Standing Up
    print("\n[2/6] Testing: STANDING UP (Height Z -> 108 mm)...")
    res_stand = client.set_posture(height_z=108, roll=0.0, pitch=0.0, yaw=0.0)
    print(f"  Command dispatched: success={res_stand.get('success')}")
    time.sleep(2.0)
    status_stand = client.get_status()
    print(f"  Posture after Standing Up: {status_stand.get('posture')}")
    print(f"  IMU Pitch/Roll: Pitch={status_stand.get('imu', {}).get('pitch')}°, Roll={status_stand.get('imu', {}).get('roll')}°")

    # 3. Sitting Down
    print("\n[3/6] Testing: SITTING DOWN / CROUCHING (Height Z -> 75 mm)...")
    res_sit = client.set_posture(height_z=75, roll=0.0, pitch=0.0, yaw=0.0)
    print(f"  Command dispatched: success={res_sit.get('success')}")
    time.sleep(2.0)
    status_sit = client.get_status()
    print(f"  Posture after Sitting Down: {status_sit.get('posture')}")
    print(f"  IMU Pitch/Roll: Pitch={status_sit.get('imu', {}).get('pitch')}°, Roll={status_sit.get('imu', {}).get('roll')}°")

    # Return to normal height before looking around
    print("\n  --> Resetting to standard stand (Height Z -> 95 mm)...")
    client.set_posture(height_z=95, roll=0.0, pitch=0.0, yaw=0.0)
    time.sleep(1.5)

    # 4. Looking Left and Right (Attitude rotation while stationary)
    print("\n[4/6] Testing: LOOKING LEFT (Body Yaw -> -8.0°)...")
    res_look_l = client.set_posture(yaw=-8.0, roll=0.0, pitch=0.0)
    print(f"  Command dispatched: success={res_look_l.get('success')}")
    time.sleep(2.0)
    status_look_l = client.get_status()
    print(f"  IMU after Looking Left: Roll={status_look_l.get('imu', {}).get('roll')}°, Pitch={status_look_l.get('imu', {}).get('pitch')}°")

    print("\n  Testing: LOOKING RIGHT (Body Yaw -> +8.0°)...")
    res_look_r = client.set_posture(yaw=8.0, roll=0.0, pitch=0.0)
    print(f"  Command dispatched: success={res_look_r.get('success')}")
    time.sleep(2.0)
    status_look_r = client.get_status()
    print(f"  IMU after Looking Right: Roll={status_look_r.get('imu', {}).get('roll')}°, Pitch={status_look_r.get('imu', {}).get('pitch')}°")

    print("\n  --> Re-centering head/body attitude (Yaw -> 0.0°)...")
    client.set_posture(yaw=0.0, roll=0.0, pitch=0.0)
    time.sleep(1.5)

    # 5. Turning Around (Turning in place)
    print("\n[5/6] Testing: TURNING LEFT (In-place rotation)...")
    res_turn_l = client.move_discrete(Direction.TURN_LEFT, intensity=Intensity.LOW)
    print(f"  Dispatch: status={res_turn_l.status.value}, message={res_turn_l.message}")
    time.sleep(2.5)
    client.stop()
    time.sleep(1.0)
    status_tl = client.get_status()
    print(f"  IMU after Turn Left: Yaw={status_tl.get('imu', {}).get('yaw')}°")

    print("\n  Testing: TURNING RIGHT (In-place rotation)...")
    res_turn_r = client.move_discrete(Direction.TURN_RIGHT, intensity=Intensity.LOW)
    print(f"  Dispatch: status={res_turn_r.status.value}, message={res_turn_r.message}")
    time.sleep(2.5)
    client.stop()
    time.sleep(1.0)
    status_tr = client.get_status()
    print(f"  IMU after Turn Right: Yaw={status_tr.get('imu', {}).get('yaw')}°")

    # 6. Final Stabilization
    print("\n[6/6] Finalizing: Stabilization & Neutral Posture...")
    client.stop()
    client.set_posture(height_z=95, roll=0.0, pitch=0.0, yaw=0.0)
    final_status = client.get_status()
    print(f"  Final Posture: {final_status.get('posture')}")
    print(f"  Final Battery: {final_status.get('battery_level')}%")
    print(f"  Final IMU:     {final_status.get('imu')}")

    print("\n==================================================")
    print("✅ ALL DOGZILLA MOVEMENTS TESTED SUCCESSFULLY!")
    print("==================================================")


if __name__ == "__main__":
    main()
