"""Voice prototype: fixed-command matcher and POST /api/v1/voice/command (OpenAI mocked)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.robot import protocol
from backend.robot.relay_router import QuestRelayAuth
from backend.voice.commands import match_command
from backend.voice.router import MAX_AUDIO_BYTES, create_voice_router
from backend.voice.transcribe import DEFAULT_MODEL, Transcriber, TranscriptionError

KEY = "quest-secret"


@pytest.mark.parametrize(
    "text, command, args",
    [
        ("stop", "stop", {}),
        ("Stop!", "stop", {}),
        ("emergency stop", "emergency_stop", {}),
        ("Emergency, stop.", "emergency_stop", {}),
        ("e-stop", "emergency_stop", {}),
        # stop wins over everything else, anywhere in the utterance
        ("forward ten seconds and then stop", "stop", {}),
        ("turn left stop", "stop", {}),
        ("stop forward 5 seconds", "stop", {}),
        ("forward then emergency stop", "emergency_stop", {}),
        ("forward", "forward", {"seconds": 1.0}),
        ("forward 3 seconds", "forward", {"seconds": 3.0}),
        ("go forward for two seconds", "forward", {"seconds": 2.0}),
        ("back 2.5 seconds", "back", {"seconds": 2.5}),
        ("backward five seconds", "back", {"seconds": 5.0}),
        ("forward 999 seconds", "forward", {"seconds": 10.0}),  # clamped
        ("turn left", "turn_left", {"degrees": 90.0}),
        ("turn right 45 degrees", "turn_right", {"degrees": 45.0}),
        ("turn left ninety degrees", "turn_left", {"degrees": 90.0}),
        ("turn right 720 degrees", "turn_right", {"degrees": 180.0}),  # clamped
        ("Turn Left.", "turn_left", {"degrees": 90.0}),
    ],
)
def test_matcher_table(text, command, args):
    result = match_command(text)
    assert result.command == command
    assert result.args == args
    assert result.matched


@pytest.mark.parametrize("text", ["", "   ", "hello there", "dance", "the stopwatch", "left", "turn"])
def test_unrecognised_does_not_match(text):
    result = match_command(text)
    assert result.command is None and result.args == {} and not result.matched


class FakeTranscriptions:
    def __init__(self, text="forward 2 seconds", exc=None):
        self.text, self.exc, self.calls = text, exc, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return type("R", (), {"text": self.text})()


def make_client(text="forward 2 seconds", exc=None, key=KEY):
    fake = FakeTranscriptions(text, exc)
    client = type("C", (), {"audio": type("A", (), {"transcriptions": fake})()})()
    class FakeGateway:
        async def run_bounded(self, direction, seconds, speed, *, source):
            return {"status": "ACCEPTED", "direction": direction, "seconds": seconds, "speed": speed, "source": source}
        async def turn(self, degrees, *, source):
            return {"status": "ACCEPTED", "degrees": degrees, "source": source}
        async def stop(self, *, source):
            return {"status": "ACCEPTED", "source": source}
        async def emergency_stop(self):
            return {"status": "ACCEPTED"}
    class FakeRunner:
        async def start(self, goal):
            self.goal = goal
        async def cancel(self, *, stop=True):
            self.cancelled = True
            return True
        def snapshot(self):
            return {"state": "running", "goal": getattr(self, "goal", None)}
    gateway, runner = FakeGateway(), FakeRunner()
    app = FastAPI()
    app.include_router(create_voice_router(QuestRelayAuth(key), Transcriber(client=client), gateway, runner))
    fake.gateway, fake.runner = gateway, runner
    return TestClient(app), fake


def post(client, data=b"RIFFfake", headers=None):
    headers = {protocol.AUTH_HEADER: KEY} if headers is None else headers
    return client.post(
        "/api/v1/voice/command", files={"audio": ("clip.wav", data, "audio/wav")}, headers=headers
    )


def test_command_matched():
    client, fake = make_client("Forward 2 seconds")
    response = post(client)
    assert response.status_code == 200
    assert response.json() == {
        "transcript": "Forward 2 seconds", "command": "forward", "args": {"seconds": 2.0}, "matched": True,
        "command_result": {"status": "ACCEPTED", "direction": "forward", "seconds": 2.0, "speed": 0.3, "source": "voice"},
        "task": None,
    }
    assert fake.calls[0]["model"] == DEFAULT_MODEL
    assert fake.calls[0]["file"][1] == b"RIFFfake"


def test_unrecognised_speech_is_not_matched():
    response = post(make_client("blah blah")[0])
    body = response.json()
    assert body["transcript"] == "blah blah" and body["command"] is None and not body["matched"]
    assert body["task"] == {"state": "running", "goal": "blah blah"}


def test_stop_in_router():
    assert post(make_client("go forward, stop")[0]).json()["command"] == "stop"


def test_oversized_clip_rejected_413():
    client, fake = make_client()
    assert post(client, b"x" * (MAX_AUDIO_BYTES + 1)).status_code == 413
    assert fake.calls == []  # never sent to OpenAI


def test_empty_clip_rejected_400():
    assert post(make_client()[0], b"").status_code == 400


def test_missing_or_wrong_key_is_401():
    client, fake = make_client()
    assert post(client, headers={}).status_code == 401
    assert post(client, headers={protocol.AUTH_HEADER: "nope"}).status_code == 401
    assert fake.calls == []


def test_auth_not_configured_fails_closed_503():
    assert post(make_client(key=None)[0]).status_code == 503


def test_transcription_failure_is_502_without_leaking_details():
    client, _ = make_client(exc=RuntimeError("sk-secret leaked?"))
    response = post(client)
    assert response.status_code == 502
    assert "sk-secret" not in response.text


def test_missing_openai_key_is_503(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.include_router(create_voice_router(QuestRelayAuth(KEY), Transcriber()))
    assert post(TestClient(app)).status_code == 503


def test_transcriber_error_type():
    import asyncio

    client = type("C", (), {"audio": type("A", (), {"transcriptions": FakeTranscriptions(exc=ValueError())})()})()
    with pytest.raises(TranscriptionError):
        asyncio.run(Transcriber(client=client).transcribe(b"x", "a.wav", "audio/wav"))


def test_app_registers_voice_route(monkeypatch):
    monkeypatch.setenv("QUEST_API_KEY", KEY)
    from backend.main import app

    assert "/api/v1/voice/command" in app.openapi()["paths"]
