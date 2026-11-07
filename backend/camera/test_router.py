"""Tests for camera auth, router and factory (FastAPI TestClient)."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .auth import CameraAuth
from .config import CameraConfig
from .factory import build_camera_service
from .router import create_camera_router

KEY = "s3cret-test-key"


def make_app(config: CameraConfig, key=KEY):
    service = build_camera_service(config)
    service.start()
    app = FastAPI()
    app.include_router(create_camera_router(lambda: service, CameraAuth(key)))
    return app, service


@pytest.fixture
def env():
    config = CameraConfig(device="synthetic", target_fps=30, max_viewers=1)
    app, service = make_app(config)
    with TestClient(app) as client:
        yield client, service
    service.stop()


H = {"X-Camera-Key": KEY}


def test_auth_rejected_without_or_with_wrong_token(env):
    client, _ = env
    for path in ("status", "snapshot", "stream"):
        assert client.get(f"/api/v1/camera/{path}").status_code == 401
        assert client.get(f"/api/v1/camera/{path}", headers={"X-Camera-Key": "bad"}).status_code == 401
        assert client.get(f"/api/v1/camera/{path}?token=bad").status_code == 401


def test_auth_header_and_query(env):
    client, _ = env
    assert client.get("/api/v1/camera/status", headers=H).status_code == 200
    assert client.get(f"/api/v1/camera/status?token={KEY}").status_code == 200


def test_any_valid_credential_wins_over_stale_one(env):
    client, _ = env
    bad = {"X-Camera-Key": "stale"}
    assert client.get(f"/api/v1/camera/status?token={KEY}", headers=bad).status_code == 200
    assert client.get("/api/v1/camera/status?token=stale", headers=H).status_code == 200
    assert client.get(f"/api/v1/camera/status?token=stale&token={KEY}").status_code == 200
    assert client.get("/api/v1/camera/status?token=stale", headers=bad).status_code == 401


def test_cookie_is_not_a_credential(env):
    client, _ = env
    client.cookies.set("camera_token", KEY)
    assert client.get("/api/v1/camera/status").status_code == 401


def test_unconfigured_key_fails_closed():
    app, service = make_app(CameraConfig(device="synthetic"), key=None)
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/camera/status", headers=H).status_code == 503
    finally:
        service.stop()


def test_status_and_snapshot(env):
    client, _ = env
    r = client.get("/api/v1/camera/status", headers=H)
    body = r.json()
    assert body["connected"] and body["capturing"]
    assert body["resolution"] == {"width": 640, "height": 480}
    assert r.headers["cache-control"] == "no-store"
    r = client.get("/api/v1/camera/snapshot", headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"


def test_stream_returns_mjpeg_and_shutdown_releases_viewer(env):
    client, service = env
    with client.stream("GET", "/api/v1/camera/stream", headers=H) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        assert r.headers["cache-control"] == "no-store"
        chunks = r.iter_bytes()
        assert next(chunks).startswith(b"--frame")
        assert service.status().active_viewers == 1
        service.stop()  # shutdown ends the stream and frees the viewer
        for _ in chunks:
            pass
    assert service.status().active_viewers == 0


def test_client_disconnect_releases_viewer_on_real_server():
    """TestClient cannot emulate disconnects, so use a real uvicorn server."""
    import socket
    import threading

    import httpx
    import uvicorn

    app, service = make_app(CameraConfig(device="synthetic", target_fps=30))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 5
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        url = f"http://127.0.0.1:{port}/api/v1/camera/stream?token={KEY}"
        with httpx.stream("GET", url) as r:
            next(r.iter_bytes())
            assert service.status().active_viewers == 1
        deadline = time.time() + 3
        while service.status().active_viewers and time.time() < deadline:
            time.sleep(0.05)
        assert service.status().active_viewers == 0
        assert service.status().capturing  # other viewers/capture unaffected
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        service.stop()


def test_response_dropped_before_iteration_releases_viewer(env):
    """A client that vanishes before the body starts must not leak a slot."""
    import asyncio

    client, service = env
    app = client.app

    async def run():
        sent = []

        async def receive():
            await asyncio.sleep(10)  # never disconnects by itself

        async def send(message):
            sent.append(message["type"])
            if message["type"] == "http.response.start":
                raise OSError("client went away")  # vanished before the body starts

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/api/v1/camera/stream", "raw_path": b"/api/v1/camera/stream",
            "query_string": b"", "root_path": "", "server": ("test", 80),
            "client": ("test", 1), "app": app,
            "headers": [(b"x-camera-key", KEY.encode())],
        }
        with pytest.raises(Exception) as excinfo:
            await app(scope, receive, send)
        # excinfo keeps the traceback (and the response) alive, so the slot
        # must have been released deterministically, not by garbage collection.
        assert service.status().active_viewers == 0
        assert excinfo.value is not None

    asyncio.run(run())


def test_viewer_limit_returns_429(env):
    client, service = env  # max_viewers=1
    with client.stream("GET", "/api/v1/camera/stream", headers=H) as first:
        next(first.iter_bytes())
        assert client.get("/api/v1/camera/stream", headers=H).status_code == 429


def test_unavailable_camera_returns_503_but_status_ok(env):
    client, service = env
    service.stop()
    assert client.get("/api/v1/camera/snapshot", headers=H).status_code == 503
    assert client.get("/api/v1/camera/stream", headers=H).status_code == 503
    r = client.get("/api/v1/camera/status", headers=H)
    assert r.status_code == 200 and r.json()["connected"] is False


def test_auth_from_env():
    assert CameraAuth.from_env({"CAMERA_API_KEY": "k"}).configured
    assert not CameraAuth.from_env({}).configured
    assert not CameraAuth.from_env({"CAMERA_API_KEY": "  "}).configured
