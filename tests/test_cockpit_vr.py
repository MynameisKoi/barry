"""WebXR cockpit (Unity layout port): page, vendored assets, camera relay URL and the client-log endpoint."""

import re

from fastapi.testclient import TestClient

from backend.main import app, dog_video_url


def test_cockpit_vr_uses_vendored_three_and_unity_layout():
    with TestClient(app) as client:
        html = client.get("/cockpit/vr").text
        # Modern three.js is self-hosted (works offline on the hotspot); the old r128 build is gone.
        assert '"three": "/static/vendor/three/three.module.js"' in html
        assert "three.min.js" not in html
        # The page never asks for WebXR Layers (r128 + 'layers' rendered black on Quest).
        assert "'layers'" not in html and '"layers"' not in html
        # Every Unity DashboardSceneBuilder panel exists.
        for name in ("header", "camera", "status", "actions", "strip", "mission", "banner", "brain"):
            assert f'new Panel("{name}"' in html
        # Unity placement constants.
        assert "placeUnity(P.header.group, 0, EYE + 0.588, 2.0)" in html
        assert "placeOnArc(P.status.group, -SIDE_PANEL_YAW, 1.3, EYE)" in html
        assert "placeOnArc(P.brain.group, 46, 1.25, EYE + 0.1)" in html
        for path in ("three/three.module.js", "three/three.core.js", "fonts/Inter.ttf", "fonts/JetBrainsMono.ttf", "fonts/Orbitron.ttf"):
            assert client.get(f"/static/vendor/{path}").status_code == 200, path


def test_cockpit_vr_safety_rules_present():
    html = TestClient(app).get("/cockpit/vr").text
    assert "const DEAD_ZONE = 0.12" in html and "const SEND_HZ = 10" in html
    assert "const ESTOP_HOLD_S = 0.3" in html                  # both grips + A
    assert re.search(r"release = explicit stop", html)          # stop on stick release
    assert "state.needCentre = true" in html                    # no auto-resume after a stop
    assert '"Controller lost"' in html and '"VR session ended"' in html


def test_dog_video_url_prefers_explicit_then_robot_host(monkeypatch):
    monkeypatch.setenv("DOG_VIDEO_URL", "http://10.0.0.5:6500/video_feed")
    assert dog_video_url() == "http://10.0.0.5:6500/video_feed"
    monkeypatch.delenv("DOG_VIDEO_URL")
    monkeypatch.setenv("ROBOT_REMOTE_URL", "http://192.168.0.174:8100")
    assert dog_video_url() == "http://192.168.0.174:6500/video_feed"
    monkeypatch.delenv("ROBOT_REMOTE_URL")
    assert dog_video_url() == "http://192.168.137.236:6500/video_feed"


def test_client_log_roundtrip_is_bounded():
    with TestClient(app) as client:
        for i in range(105):
            assert client.post("/api/debug/client-log", json={"level": "error", "message": f"m{i}" + "x" * 3000}).json() == {"ok": True}
        entries = client.get("/api/debug/client-log").json()["entries"]
        assert len(entries) == 100
        assert entries[-1]["message"].startswith("m104") and len(entries[-1]["message"]) == 1500
