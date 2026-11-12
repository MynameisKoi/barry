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
        assert "placeUnity(screenRoot, 0, EYE, 2.0)" in html and "P.header.group.position.set(0, 0.588, 0)" in html
        # Panels can be moved in VR (grip) and the layout is saved / reset.
        assert 'addEventListener("squeezestart", () => startGrab(c))' in html
        assert "barry.vr.layout.v1" in html and "function resetLayout()" in html
        assert "if (grabbing) { lx = ly = rx = 0; }" in html      # no driving while moving a panel
        assert "placeOnArc(P.status.group, -SIDE_PANEL_YAW, 1.3, EYE)" in html
        assert "placeOnArc(P.brain.group, 46, 1.25, EYE + 0.1)" in html
        assert "const STRIP_BELOW_DEG = 35, STRIP_TILT_DEG = 30;" in html
        for path in ("three/three.module.js", "three/three.core.js", "fonts/Inter.ttf", "fonts/JetBrainsMono.ttf", "fonts/Orbitron.ttf"):
            assert client.get(f"/static/vendor/{path}").status_code == 200, path


def test_cockpit_vr_safety_rules_present():
    html = TestClient(app).get("/cockpit/vr").text
    assert "const DEAD_ZONE = 0.12" in html and "const SEND_HZ = 10" in html
    assert "const ESTOP_HOLD_S = 0.3" in html                  # both grips + A
    assert re.search(r"release = explicit stop", html)          # stop on stick release
    assert "state.needCentre = true" in html                    # no auto-resume after a stop
    assert '"Controller lost"' in html and '"VR session ended"' in html


def test_cockpit_vr_detection_overlay_uses_fresh_normalized_boxes():
    html = TestClient(app).get("/cockpit/vr").text
    assert 'const DETECTION_POLL_MS = 333, DETECTION_MAX_AGE_S = 2' in html
    assert 'fetch("/api/ai/detections", { cache: "no-store" })' in html
    assert 'payload?.status === "available"' in html
    assert 'const [top, left, bottom, right] = box' in html
    assert 'const [top, left, bottom, right] = detection.box' in html
    assert 'typeof age !== "number"' in html and 'typeof v === "number" && Number.isFinite(v)' in html
    assert 'const rect = containedVideoRect(sourceW, sourceH)' in html
    assert 'rect.x + left * rect.w' in html and 'rect.y + top * rect.h' in html
    assert 'age_s: age' in html and 'd.age_s + elapsed <= DETECTION_MAX_AGE_S' in html
    assert 'state.detectionStatus !== "available" || !state.cameraLive' in html
    assert 'detectionOn ? `DETECT · ${detections.length}` : "DETECT OFF"' in html
    assert 'if (signature !== state.detectionsSignature)' in html


def test_cockpit_vr_contained_video_rect_handles_wide_and_tall_frames():
    html = TestClient(app).get("/cockpit/vr").text
    assert 'const VIDEO_INSET = 10, VIDEO_INNER_W = 860, VIDEO_INNER_H = 475' in html
    assert 'if (vh > VIDEO_INNER_H) { vh = VIDEO_INNER_H; vw = VIDEO_INNER_H * aspect; }' in html
    assert 'return { x: VIDEO_INSET + (VIDEO_INNER_W - vw) / 2, y: VIDEO_INSET + (VIDEO_INNER_H - vh) / 2, w: vw, h: vh }' in html
    assert 'videoMesh.scale.set(rect.w * S_FAR / 1000, rect.h * S_FAR / 1000, 1)' in html


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


def test_cockpit_vr_push_to_talk():
    html = TestClient(app).get("/cockpit/vr").text
    assert 'fetch("/api/v1/voice/command", { method: "POST", body: form })' in html
    assert 'form.append("audio", blob' in html                   # field name the voice router expects
    assert "const VOICE_HOLD_S = 0.35, VOICE_MAX_MS = 15000;" in html  # hold Y; router caps clips at 15 s
    assert "Y: tap toggles the Brain panel (Unity), hold >= 0.35 s = push-to-talk." in html
    assert '["Y", "Hold: talk"' in html                          # controller tooltip
    assert 'id="talk"' in html                                    # desktop hold-to-talk button


def test_cockpit_vr_talk_button_on_control_strip():
    html = TestClient(app).get("/cockpit/vr").text
    assert 'p.hold("talk", 728, 28, 100, 52, startTalking, stopTalking)' in html      # point + hold trigger
    assert 'target.addEventListener("selectend", () => { if (c.holding)' in html      # release sends the clip
    assert 'p.button("exit", 616, 28, 100, 52' in html and 'p.button("end", 840, 28, 100, 52' in html
    assert "if (c.holding) { releaseTarget(c.holding); c.holding = null; }" in html  # controller lost mid-hold
