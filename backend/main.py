"""BARRY - AI-Powered Immersive Robotic Inspection System Backend.

FastAPI main application serving REST APIs and real-time WebSocket
for Meta Quest 3S VR command center, Flipper Zero NFC authorization,
DOGZILLA quadruped locomotion, and the BARRY AI Intelligence Engine.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.ai.detection import detection_service
from backend.ai.orchestrator import AIOrchestrator
from backend.ai.schemas import (
    AIAnalysisRecord,
    AIProviderStatus,
    AnalyzeEventRequest,
    InspectionEventIngest,
    InspectionEventResponse,
    MissionReport,
)
from backend.api.auth import router as auth_router
from backend.api.missions import router as missions_router
from backend.auth.authorization import auth_service
from backend.auth.flipper_listener import FlipperListener
from backend.camera.auth import CameraAuth
from backend.camera.config import CameraConfig
from backend.camera.factory import build_camera_service
from backend.camera.interface import CameraUnavailableError
from backend.camera.router import create_camera_router
from backend.database import db
from backend.events.logger import EventLogger
from backend.models import (
    AuthScanRequest,
    AuthScanResponse,
    DetectionAlert,
    EventRecord,
    MissionStartRequest,
    MissionStatusResponse,
    MissionStopRequest,
    OperatorResponse,
    RobotCommandRequest,
    RobotStatusResponse,
)
from backend.robot.dogzilla import DogzillaController
from backend.robot.factory import create_robot
from backend.robot.interface import RobotInterface
from backend.robot.server import PostureUpdateRequest
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

# Core Services Initialization
ROBOT_MODE = os.getenv("ROBOT_MODE", "simulation").lower()
robot: RobotInterface = create_robot(mode=ROBOT_MODE)
event_logger = EventLogger(db_manager=db)
ai_orchestrator = AIOrchestrator(db_manager=db)
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


main_event_loop: Optional[asyncio.AbstractEventLoop] = None


def on_flipper_tag_scanned(raw_uid: str):
    """Callback invoked when Flipper Zero detects an NFC tag over USB serial."""
    authorized, operator_data, message = auth_service.verify_tag_uid(raw_uid)
    logger.info("Flipper USB scan: %s -> %s", raw_uid, message)
    if main_event_loop and main_event_loop.is_running():
        payload = {
            "type": "auth_event",
            "source": "flipper_zero_usb",
            "authorized": authorized,
            "operator": operator_data,
            "message": message,
            "tag_uid": raw_uid,
        }
        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(payload), main_event_loop)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_event_loop
    main_event_loop = asyncio.get_running_loop()

    # Startup: ensure DB tables, start telemetry and Flipper listener
    db.init_db()
    telemetry_task = asyncio.create_task(telemetry_broadcaster())
    flipper = FlipperListener(on_tag_detected=on_flipper_tag_scanned)
    flipper.start()

    # Camera Feed API (v1): one shared capture service for all viewers.
    # A missing or failed camera must not take the whole backend down; the
    # status endpoint reports it as disconnected instead.
    camera_config = CameraConfig.from_env()
    app.state.camera = build_camera_service(camera_config)
    if camera_config.enabled:
        try:
            # start() blocks while it retries, so keep it off the event loop.
            await asyncio.to_thread(app.state.camera.start)
        except CameraUnavailableError as exc:
            logger.warning("Camera unavailable at startup: %s", exc)

    yield

    await asyncio.to_thread(app.state.camera.stop)

    # Shutdown: stop background listeners
    telemetry_task.cancel()
    flipper.stop()
    try:
        await telemetry_task
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

app.include_router(auth_router)
app.include_router(missions_router)
# Camera Feed API v1: /api/v1/camera/{stream,snapshot,status}, token-protected
# via CAMERA_API_KEY (fails closed with 503 if unset).
app.include_router(create_camera_router(lambda: app.state.camera, CameraAuth.from_env()))


# ============================================================================
# Root & Health Endpoints
# ============================================================================

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", tags=["Health"])
async def root():
    return {
        "system": "BARRY - AI-Powered Immersive Robotic Inspection System",
        "status": "online",
        "mode": robot.get_status()["mode"],
        "version": "1.0.0",
        "cockpit_url": "/cockpit",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/cockpit", tags=["Cockpit"], response_class=FileResponse)
async def get_cockpit():
    """Serves the interactive cybernetic web command cockpit."""
    html_path = STATIC_DIR / "cockpit.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Cockpit UI not found.")
    return FileResponse(html_path)


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

    # Log command or emergency stop event
    event_type = "emergency_stop" if action == "emergency_stop" else "robot_command"
    mission_service.log_event(
        event_type=event_type,
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


@app.post("/api/robot/stop", tags=["Robot"])
async def stop_robot_endpoint():
    """Brings the robot to a controlled stop."""
    result = robot.stop()
    mission_service.log_event(
        event_type="robot_command",
        description="Action: stop (controlled stop)",
    )
    await ws_manager.broadcast({"type": "robot_status", "telemetry": robot.get_status()})
    return result


@app.post("/api/robot/posture", tags=["Robot"])
async def set_robot_posture(payload: PostureUpdateRequest):
    """Updates DOGZILLA body height (z), attitude (roll/pitch/yaw), gait_type, pace, or preset."""
    result = robot.set_posture(
        height_z=payload.height_z,
        roll=payload.roll,
        pitch=payload.pitch,
        yaw=payload.yaw,
        gait_type=payload.gait_type,
        pace=payload.pace,
        imu_enabled=payload.imu_enabled,
        preset=payload.preset,
    )
    mission_service.log_event(
        event_type="robot_posture",
        description=f"Posture update: {payload.model_dump(exclude_none=True)}, Success: {result.get('success')}",
    )
    await ws_manager.broadcast({"type": "robot_status", "telemetry": robot.get_status()})
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Posture update failed."),
        )
    return result


# ============================================================================
# 4. Audit Trail & Structured Inspection Events Endpoints
# ============================================================================

@app.post("/api/events/ingest", response_model=InspectionEventResponse, tags=["Audit & Events"])
async def ingest_inspection_event(payload: InspectionEventIngest):
    """
    Ingests a structured inspection event from DOGZILLA or sensors, persists it to SQLite,
    runs optional AI analysis, and broadcasts updates over WebSocket.
    Failed AI requests never block or fail event ingestion.
    """
    try:
        stored_event = event_logger.ingest_event(
            mission_id=payload.mission_id,
            source=payload.source,
            event_type=payload.event_type,
            data=payload.data,
            event_uuid=payload.event_uuid,
        )
    except ValueError as ve:
        msg = str(ve)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    await ws_manager.broadcast(
        {
            "type": "inspection_event",
            "event": stored_event,
        }
    )

    # If duplicate event already has ai_analysis attached, return it directly
    if stored_event.get("ai_analysis") is not None:
        return InspectionEventResponse(**stored_event)

    if payload.auto_analyze:
        analysis_record = await ai_orchestrator.analyze_inspection_event(
            event_payload=stored_event,
            mission_id=stored_event["mission_id"],
            event_id=stored_event["id"],
        )
        stored_event["ai_analysis"] = analysis_record
        if analysis_record.analysis is not None:
            await ws_manager.broadcast(
                {
                    "type": "ai_alert",
                    "mission_id": stored_event["mission_id"],
                    "event_id": stored_event["id"],
                    "risk_level": analysis_record.risk_level,
                    "summary": analysis_record.analysis.summary,
                    "recommended_action": analysis_record.analysis.recommended_action,
                    "requires_human_review": analysis_record.analysis.requires_human_review,
                    "detected_objects": analysis_record.analysis.detected_objects,
                    "provider": analysis_record.provider,
                    "model": analysis_record.model,
                    "timestamp": analysis_record.created_at,
                }
            )

    return InspectionEventResponse(**stored_event)


@app.get("/api/events", response_model=List[EventRecord], tags=["Audit & Events"])
async def get_event_history(
    mission_id: Optional[str] = Query(None, description="Filter by mission ID"),
    limit: int = Query(50, ge=1, le=500),
):
    """Returns audit log of missions, NFC authentications, commands, and AI alerts."""
    return mission_service.get_events(mission_id=mission_id, limit=limit)


@app.get("/api/events/structured", tags=["Audit & Events"])
async def get_structured_events(
    mission_id: Optional[str] = Query(None, description="Filter by mission ID"),
    limit: int = Query(50, ge=1, le=500),
):
    """Returns structured inspection events stored in `inspection_events`."""
    return event_logger.list_inspection_events(mission_id=mission_id, limit=limit)


@app.get("/api/events/{event_id}", tags=["Audit & Events"])
async def get_event_by_id(event_id: int):
    """Returns a specific structured inspection event and its AI analysis by ID."""
    insp_event = event_logger.get_inspection_event(event_id)
    if insp_event:
        return insp_event

    # Fallback check in legacy audit `events` table
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM events WHERE event_id = ?", (event_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Event '{event_id}' not found.",
    )


# ============================================================================
# 5. AI Intelligence Engine & Detection Endpoints
# ============================================================================

@app.post("/api/ai/analyze", response_model=AIAnalysisRecord, tags=["AI Inspection"])
async def analyze_inspection_data(request: AnalyzeEventRequest):
    """
    Triggers BARRY AI analysis on an existing `event_id` or an ad-hoc inspection payload.
    Validates output with Pydantic, stores in `ai_analyses`, and broadcasts `ai_alert`.
    """
    if request.event_id is not None:
        stored = event_logger.get_inspection_event(request.event_id)
        if not stored:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Inspection event '{request.event_id}' not found.",
            )
        target_payload = stored
        target_mission_id = request.mission_id or stored.get("mission_id")
        target_event_id = request.event_id
    else:
        target_mission_id = request.mission_id
        target_event_id = None
        target_payload = {
            "mission_id": target_mission_id,
            "source": request.source,
            "event_type": request.event_type,
            "data": request.data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    analysis_record = await ai_orchestrator.analyze_inspection_event(
        event_payload=target_payload,
        mission_id=target_mission_id,
        event_id=target_event_id,
    )

    if analysis_record.analysis is not None:
        await ws_manager.broadcast(
            {
                "type": "ai_alert",
                "mission_id": analysis_record.mission_id,
                "event_id": analysis_record.event_id,
                "risk_level": analysis_record.risk_level,
                "summary": analysis_record.analysis.summary,
                "recommended_action": analysis_record.analysis.recommended_action,
                "requires_human_review": analysis_record.analysis.requires_human_review,
                "detected_objects": analysis_record.analysis.detected_objects,
                "provider": analysis_record.provider,
                "model": analysis_record.model,
                "timestamp": analysis_record.created_at,
            }
        )

    return analysis_record


@app.post("/api/ai/report/{mission_id}", response_model=MissionReport, tags=["AI Inspection"])
async def generate_mission_report(mission_id: str):
    """Generates a comprehensive AI-assisted inspection report for a mission."""
    try:
        report = await ai_orchestrator.generate_mission_report(mission_id)
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(ve),
        )

    await ws_manager.broadcast(
        {
            "type": "mission_report",
            "mission_id": report.mission_id,
            "risk_level": report.ai_assessment.risk_level,
            "summary": report.ai_assessment.summary,
            "generated_at": report.generated_at,
        }
    )
    return report


@app.get("/api/ai/status", response_model=AIProviderStatus, tags=["AI Inspection"])
async def get_ai_provider_status():
    """Returns active LLM provider, fallback configuration, model, and readiness status."""
    return await ai_orchestrator.get_provider_status()


@app.get("/api/ai/call-logs", tags=["AI Inspection"])
async def get_ai_call_logs(limit: int = Query(50, ge=1, le=500)):
    """Returns transparent audit logs of all outbound LLM API calls."""
    logs_file = Path(__file__).resolve().parent.parent / "logs" / "ai_calls.jsonl"
    if not logs_file.exists():
        return []
    records = []
    try:
        with open(logs_file, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    try:
                        records.append(json.loads(line_str))
                    except Exception:
                        pass
    except Exception as exc:
        logger.warning("Error reading ai_calls.jsonl: %s", exc)
    return records[-limit:]


@app.get("/api/ai/analyses", tags=["AI Inspection"])
async def list_ai_analyses(
    mission_id: Optional[str] = Query(None, description="Filter by mission ID"),
    event_id: Optional[int] = Query(None, description="Filter by inspection event ID"),
    limit: int = Query(50, ge=1, le=500),
):
    """Returns stored AI analysis records from SQLite."""
    return ai_orchestrator.list_analyses(mission_id=mission_id, event_id=event_id, limit=limit)


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
# 6. Camera Streaming & Video Feeds
# ============================================================================

# Camera: served by the token-protected Camera Feed API v1 mounted above
# (GET /api/v1/camera/{stream,snapshot,status}); the old unauthenticated
# /api/camera/* routes were removed.


# ============================================================================
# 7. Real-time WebSocket
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
