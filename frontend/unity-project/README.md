# BARRY - Unity VR Operator Command Center

This directory hosts the Unity project targeted at **Meta Quest 3S** using the **Meta XR SDK** and Unity 6 (Android Build Target).

---

## Command Center Panels
1. **Robot Camera Panel**: Displays DOGZILLA video feed and AI detection bounding boxes.
2. **Robot Control Panel**: Virtual buttons / thumbstick mappings for Forward, Backward, Turn Left, Turn Right, Stop, and Emergency Stop.
3. **Mission Panel**: Shows active Mission ID, Authorized Operator name & role, and Target Inspection Zone.
4. **AI Alert Panel**: Real-time notifications for detected objects, confidence rating, zone, and timestamp.
5. **System Telemetry Panel**: Shows Robot online/offline state, battery voltage/percentage, and WebSocket connection status.

---

## Connection Setup
Set the backend endpoint in your Unity networking manager:
- `Backend REST URL`: `http://<LAPTOP_LAN_IP>:8000`
- `WebSocket URL`: `ws://<LAPTOP_LAN_IP>:8000/ws`

*Note: Do not use `localhost` inside Quest standalone builds. Always use the laptop's Wi-Fi LAN IP address.*
