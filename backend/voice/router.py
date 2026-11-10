"""``POST /api/v1/voice/command``: push-to-talk clip -> transcript -> fixed command (dry run)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from backend.robot.relay_router import QuestRelayAuth
from backend.voice.commands import match_command
from backend.voice.transcribe import Transcriber, TranscriptionError, TranscriptionNotConfigured

logger = logging.getLogger(__name__)

# Push-to-talk clips are <= 15 s. Duration can't be read without decoding, so cap the upload
# size (15 s of 48 kHz 16-bit stereo PCM is ~2.9 MB; compressed formats are far smaller).
MAX_AUDIO_BYTES = 3_000_000


def create_voice_router(
    auth: QuestRelayAuth | None = None,
    transcriber: Transcriber | None = None,
) -> APIRouter:
    transcriber = transcriber or Transcriber()
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
        # TODO(gateway): when the command gateway (#57) exists, send `result` to it here.
        # Dry run only: nothing is sent to the robot.
        return {
            "transcript": transcript,
            "command": result.command,
            "args": result.args,
            "matched": result.matched,
        }

    return router
