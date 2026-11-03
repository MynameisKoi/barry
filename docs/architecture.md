# BARRY System Architecture

## Overview
**BARRY** is an AI-powered immersive robotic inspection platform uniting three core hardware platforms:
1. **Flipper Zero**: Physical mission credential demonstration via NFC tag reading.
2. **Meta Quest 3S**: Immersive VR operator command center for viewing camera feeds, telemetry, and issuing locomotion controls.
3. **DOGZILLA S1/S2**: Physical quadruped robotic dog carrying onboard sensors for inspection missions.

---

## Topology Diagram

```
+-------------------------------------------------------------+
|                      META QUEST 3S                          |
|         (VR Command Center / Headset Display)               |
+-------------------------------------------------------------+
                               |
             WebSocket / HTTP  | Wi-Fi LAN
                               v
+-------------------------------------------------------------+
|               PRIMARY LAPTOP GATEWAY & BACKEND              |
|                                                             |
|  +-------------------------------------------------------+  |
|  |                     FastAPI API                       |  |
|  |  * REST Endpoints (/api/auth, /api/mission, /api/robot) |
|  |  * Real-Time WebSocket (/ws)                          |  |
|  +-------------------------------------------------------+  |
|                               |                             |
|  +----------------------------+--------------------------+  |
|  |             |              |             |            |  |
|  v             v              v             v            v  |
| [Auth]     [Mission]      [Locomotion]     [AI]       [DB]  |
| Flipper    Lifecycle &    RobotInterface  YOLO &    SQLite  |
| NFC Tags   Audit Events   Sim / DOGZILLA  Vision    Events  |
+-------------------------------------------------------------+
               |                               |
               | USB / Wi-Fi                   | Serial / ROS2
               v                               v
+-----------------------------+ +-----------------------------+
|        FLIPPER ZERO         | |     DOGZILLA S1/S2 ROBOT    |
|   NFC Test Card Reader      | |  Quadruped Locomotion & Cam |
+-----------------------------+ +-----------------------------+
```

---

## Architectural Principles

1. **Hardware Decoupling via RobotInterface**:
   The backend communicates with robots through an abstract `RobotInterface`.
   The `RobotSimulator` allows developing and testing the Meta Quest 3S VR application and FastAPI endpoints before physical DOGZILLA hardware arrives.
   When physical DOGZILLA arrives, `DogzillaController` plugs in without changing Unity code or backend REST/WebSocket contracts.

2. **Laptop as Central Processing Unit**:
   Heavy tasks (YOLO object detection inference, database persistence, and WebSocket broadcasting) run on the operator laptop to preserve battery and compute on the robotic platform.

3. **Same Subnet Local Area Network**:
   The Meta Quest 3S and laptop communicate over local Wi-Fi. Quest connects directly to the laptop's local IP (e.g. `ws://192.168.1.X:8000/ws` and `http://192.168.1.X:8000`).

4. **Fail-Safe & Safety Architecture**:
   - `emergency_stop` cuts locomotion immediately and sets an explicit hardware/software lock.
   - Movements require active mission authorization.
