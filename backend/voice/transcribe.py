"""Speech-to-text through the OpenAI transcription API (cloud; called from the laptop)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

OPENAI_KEY_ENV = "OPENAI_API_KEY"
MODEL_ENV = "OPENAI_TRANSCRIBE_MODEL"
# Current model per the installed openai SDK (openai.types.AudioModel). whisper-1 and the
# gpt-4o-*-transcribe models are marked deprecated there (shutdown 2027-02-26).
DEFAULT_MODEL = "gpt-transcribe"


class TranscriptionNotConfigured(RuntimeError):
    """OPENAI_API_KEY is not set."""


class TranscriptionError(RuntimeError):
    """The transcription request failed."""


class Transcriber:
    def __init__(self, client=None, model: str | None = None) -> None:
        self._client = client
        self._model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL

    def _get_client(self):
        if self._client is None:
            key = os.environ.get(OPENAI_KEY_ENV, "").strip()
            if not key:
                raise TranscriptionNotConfigured("OPENAI_API_KEY is not set")
            from openai import AsyncOpenAI  # imported lazily so the app boots without the key

            self._client = AsyncOpenAI(api_key=key)
        return self._client

    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> str:
        client = self._get_client()
        try:
            result = await client.audio.transcriptions.create(
                model=self._model,
                file=(filename or "clip.wav", audio, content_type or "application/octet-stream"),
            )
        except Exception as exc:
            # Never include the audio or the key; the exception type is enough.
            logger.warning("Transcription request failed: %s", type(exc).__name__)
            raise TranscriptionError("transcription failed") from exc
        return (getattr(result, "text", "") or "").strip()
