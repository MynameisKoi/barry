"""BARRY - AI-Powered Immersive Robotic Inspection System Backend.

FastAPI main application serving REST APIs and real-time WebSocket
for Meta Quest 3S VR command center, Flipper Zero NFC authorization,
and DOGZILLA quadruped locomotion.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, Dict, List, Optional, Set
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware

from backend.ai.detection import detection_service
from backend.auth.authorization import auth_service
from backend.database import db
from backend.models import (
    AuthScanRequest,
    AuthScanResponse,
    DetectionAlert,
    EventRecord,
    MissionStartRequest,
    MissionStatusResponse,
    MissionStopRequest,
    OperatorResponse,
    RobotAction,
    RobotCommandRequest,
    RobotStatusResponse,
)
from backend.robot.dogzilla import DogzillaController
from backend.robot.interface import RobotInterface
from backend.robot.simulator import RobotSimulator
from backend.services.mission import mission_service

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("barry.backend")

# Connection Manager for WebSockets (Meta Quest 3S VR command center & browser clients)
class ConnectionManager:
    """Manages active WebSocket connections and broadcasts."""

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info("WebSocket client connected. Total active: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)
        logger.info("WebSocket client disconnected. Total active: %d", len(self.active_connections))

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcasts JSON payload to all connected clients."""
        if not self.active_connections:
            return
        payload = json.dumps(message)
        dead_connections = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_text(payload)
            except Exception:
                dead_connections.add(connection)
        for dead in dead_connections:
            self.active_connections.discard(dead)


ws_manager = ConnectionManager()

# Robot Controller Initialization
ROBOT_MODE = os.getenv("ROBOT_MODE", "simulation").lower()
robot: RobotInterface = RobotSimulator() if ROBOT_MODE == "simulation" else DogzillaController()
logger.info("BARRY initialized with robot controller mode: %s", ROBOT_MODE)


# Background Telemetry Task
async def telemetry_broadcaster():
    """Periodically broadcasts robot status over WebSocket to keep VR headset synced."""
    try:
        while True:
            await asyncio.sleep(1.0)
            if ws_manager.active_connections:
                status_payload = {
                    "type": "telemetry",
                    "telemetry": robot.get_status(),
                    "mission": mission_service.get_status(),
                }
                await ws_manager.broadcast(status_payload)
    except asyncio.CancelledError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB tables and start telemetry broadcaster
    db.init_db()
    task = asyncio.create_task(telemetry_broadcaster())
    yield
    # Shutdown: cancel telemetry broadcaster
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="BARRY Inspection API",
    description="Backend API for BARRY AI-Powered Immersive Robotic Inspection System",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for local development and VR/Unity network connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Root & Health Endpoints
# ============================================================================

@app.get("/", tags=["Health"])
async def root():
    return {
        "system": "BARRY - AI-Powered Immersive Robotic Inspection System",
        "status": "online",
        "mode": robot.get_status()["mode"],
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# 1. NFC Credential Authorization Endpoints
# ============================================================================

@app.post("/api/auth/scan", response_model=AuthScanResponse, tags=["Authentication"])
async def scan_nfc_credential(payload: AuthScanRequest):
    """Verifies a test NFC credential scanned via Flipper Zero or entered manually."""
    authorized, operator_data, message = auth_service.verify_tag_uid(payload.tag_uid)

    response_data = {
        "authorized": authorized,
        "operator": operator_data,
        "message": message,
    }

    # Notify VR command center via WebSocket
    await ws_manager.broadcast(
        {
            "type": "auth_event",
            "authorized": authorized,
            "operator": operator_data,
            "message": message,
            "tag_uid": payload.tag_uid,
        }
    )

    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=response_data,
        )

    return response_data


@app.get("/api/operators", response_model=List[OperatorResponse], tags=["Authentication"])
async def get_operators():
    """Returns list of registered test operators and their demo NFC UIDs."""
    return auth_service.list_operators()


# ============================================================================
# 2. Mission Management Endpoints
# ============================================================================

@app.post("/api/mission/start", response_model=MissionStatusResponse, tags=["Missions"])
async def start_mission(payload: MissionStartRequest):
    """Starts an authorized inspection mission."""
    try:
        mission_info = mission_service.start_mission(
            operator_id=payload.operator_id,
            zone=payload.zone,
        )
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(re))

    # Broadcast mission start to VR headset
    await ws_manager.broadcast({"type": "mission_update", "mission": mission_info})
    return mission_info


@app.post("/api/mission/stop", tags=["Missions"])
async def stop_mission(payload: MissionStopRequest = MissionStopRequest()):
    """Terminates the currently active inspection mission."""
    stop_info = mission_service.stop_mission(reason=payload.reason or "Normal completion")

    # Halt robot locomotion upon mission end
    robot.stop()

    await ws_manager.broadcast({"type": "mission_update", "mission": stop_info})
    return stop_info


@app.get("/api/mission/status", response_model=MissionStatusResponse, tags=["Missions"])
async def get_mission_status():
    """Returns the current inspection mission information."""
    return mission_service.get_status()


# ============================================================================
# 3. Robot Locomotion & Status Endpoints
# ============================================================================

@app.get("/api/robot/status", response_model=RobotStatusResponse, tags=["Robot"])
async def get_robot_status():
    """Returns robot connection, battery, coordinates, and operating status."""
    return robot.get_status()


@app.post("/api/robot/command", tags=["Robot"])
async def send_robot_command(command: RobotCommandRequest):
    """Sends movement command (forward, backward, left, right, stop, emergency_stop)."""
    action = command.action.lower().strip()
    result = robot.move(action=action, speed=command.speed)

    # Log command event
    mission_service.log_event(
        event_type="robot_command",
        description=f"Action: {action}, Speed: {command.speed}, Success: {result['success']}",
    )

    # Real-time WebSocket broadcast to VR interface
    await ws_manager.broadcast(
        {
            "type": "robot_command_feedback",
            "action": action,
            "speed": command.speed,
            "success": result["success"],
            "telemetry": result.get("telemetry"),
        }
    )

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["message"],
        )

    return result


@app.post("/api/robot/reset-emergency", tags=["Robot"])
async def reset_emergency_stop():
    """Clears the emergency stop lock on the robot."""
    result = robot.reset_emergency_stop()
    mission_service.log_event(
        event_type="emergency_stop_reset",
        description="Emergency stop lock cleared by operator.",
    )
    await ws_manager.broadcast({"type": "robot_status", "telemetry": robot.get_status()})
    return result


# ============================================================================
# 4. Audit Trail & Events Endpoints
# ============================================================================

@app.get("/api/events", response_model=List[EventRecord], tags=["Audit & Events"])
async def get_event_history(
    mission_id: Optional[str] = Query(None, description="Filter by mission ID"),
    limit: int = Query(50, ge=1, le=500),
):
    """Returns audit log of missions, NFC authentications, commands, and AI alerts."""
    return mission_service.get_events(mission_id=mission_id, limit=limit)


# ============================================================================
# 5. AI Detection & Alerts Endpoints
# ============================================================================

@app.post("/api/ai/trigger-detection", response_model=DetectionAlert, tags=["AI Inspection"])
async def trigger_simulated_detection(zone: Optional[str] = None):
    """Triggers an AI object detection alert (simulated/live) and pushes to VR."""
    alert = detection_service.generate_simulated_detection(zone=zone or "Restricted Inspection Zone")

    # Record detection event in database
    mission_service.log_event(
        event_type="detection",
        description=f"Detected '{alert.object}' with {int(alert.confidence * 100)}% confidence in {alert.zone}",
    )

    # Broadcast detection event via WebSocket
    await ws_manager.broadcast(alert.model_dump())
    return alert


# ============================================================================
# 6. Real-time WebSocket
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time bi-directional channel for Meta Quest 3S VR command center."""
    await ws_manager.connect(websocket)
    try:
        # Send initial handshake payload
        await websocket.send_text(
            json.dumps(
                {
                    "type": "welcome",
                    "message": "Connected to BARRY Inspection Gateway",
                    "telemetry": robot.get_status(),
                    "mission": mission_service.get_status(),
                }
            )
        )

        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
                msg_type = msg.get("type", "")

                # Handle robot commands sent directly over WebSocket
                if msg_type == "robot_command":
                    action = msg.get("action", "stop")
                    speed = float(msg.get("speed", 0.2))
                    res = robot.move(action=action, speed=speed)
                    mission_service.log_event(
                        event_type="robot_command_ws",
                        description=f"WS Action: {action}, Speed: {speed}",
                    )
                    await ws_manager.broadcast(
                        {
                            "type": "robot_command_feedback",
                            "action": action,
                            "speed": speed,
                            "success": res["success"],
                            "telemetry": res.get("telemetry"),
                        }
                    )

                elif msg_type == "emergency_stop":
                    res = robot.emergency_stop()
                    mission_service.log_event(
                        event_type="emergency_stop",
                        description="Emergency stop triggered via WebSocket.",
                    )
                    await ws_manager.broadcast(
                        {
                            "type": "emergency_stop",
                            "message": "Emergency stop triggered!",
                            "telemetry": res.get("telemetry"),
                        }
                    )

                elif msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON format."}))

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        ws_manager.disconnect(websocket)
