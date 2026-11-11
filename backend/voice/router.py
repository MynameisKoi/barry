"""``POST /api/v1/voice/command``: push-to-talk clip -> transcript -> fixed command (dry run)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from backend.robot.relay_router import QuestRelayAuth
from backend.ai.control.executor import TaskRunner
from backend.ai.control.gateway_adapter import VoiceGatewayAdapter, _shared_gateway
from backend.voice.commands import match_command
from backend.voice.transcribe import Transcriber, TranscriptionError, TranscriptionNotConfigured

logger = logging.getLogger(__name__)

# Push-to-talk clips are <= 15 s. Duration can't be read without decoding, so cap the upload
# size (15 s of 48 kHz 16-bit stereo PCM is ~2.9 MB; compressed formats are far smaller).
MAX_AUDIO_BYTES = 3_000_000


def create_voice_router(
    auth: QuestRelayAuth | None = None,
    transcriber: Transcriber | None = None,
    gateway: VoiceGatewayAdapter | None = None,
    llm_runner: TaskRunner | None = None,
) -> APIRouter:
    transcriber = transcriber or Transcriber()
    gateway = gateway or VoiceGatewayAdapter(_shared_gateway)
    router = APIRouter(
        prefix="/api/v1/voice",
        tags=["Voice"],
        dependencies=[Depends(auth or QuestRelayAuth.from_env())],
    )

    @router.post("/command")
    async def voice_command(audio: UploadFile = File(...)) -> dict[str, Any]:
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "audio clip too large (max 15 s)")
        if not data:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty audio clip")
        try:
            transcript = await transcriber.transcribe(
                data, audio.filename or "clip.wav", audio.content_type or ""
            )
        except TranscriptionNotConfigured:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "voice transcription is not configured")
        except TranscriptionError:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "transcription failed")

        logger.info("Voice transcript: %r", transcript)
        result = match_command(transcript)
        command_result: dict[str, Any] | None = None
        if result.matched:
            if result.command in {"stop", "emergency_stop"} and llm_runner is not None:
                await llm_runner.cancel(stop=False)
            if result.command == "stop":
                command_result = await gateway.stop(source="voice")
            elif result.command == "emergency_stop":
                command_result = await gateway.emergency_stop()
            elif result.command in {"forward", "back"}:
                direction = "forward" if result.command == "forward" else "backward"
                command_result = await gateway.run_bounded(
                    direction, result.args["seconds"], 0.3, source="voice"
                )
            elif result.command == "turn_left":
                command_result = await gateway.turn(-result.args["degrees"], source="voice")
            elif result.command == "turn_right":
                command_result = await gateway.turn(result.args["degrees"], source="voice")
        else:
            if llm_runner is None:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM voice conversation is not configured")
            task = await llm_runner.start(transcript)
        return {
            "transcript": transcript,
            "command": result.command,
            "args": result.args,
            "matched": result.matched,
            "command_result": command_result,
            "task": llm_runner.snapshot() if not result.matched and llm_runner else None,
        }

    return router
