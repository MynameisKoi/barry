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


def _run_find_ui_js(checks):
    """Execute the shipped find UI handlers with DOM/fetch/canvas test doubles."""
    import shutil
    import subprocess
    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for cockpit JavaScript behavior tests")
    html = TestClient(app).get("/cockpit/vr").text
    presets = html[html.index("const LLM_PRESETS = ["):html.index("// Galactic theme tokens")]
    handlers = html[html.index("const finding = () =>"):html.index("let llmPoll = null;")]
    start_goal = html[html.index("async function startGoal(goal)"):html.index("async function cancelGoal()")]
    helpers = html[html.index("function scrollBrain(lines)"):html.index('new Panel("brain",')]
    panels = html[html.index('new Panel("brain",'):html.index("// ------------------------------------------------------------------ renderer, scene, environment")]
    script = r'''
const assert = require("node:assert/strict");
const state = {brainOpen:true, llmTask:{state:"idle", steps:[]}};
const desktop = {};
const document = {getElementById:()=>desktop};
const markDirty = ()=>{};
const setFont = ()=>{};
const pollLlm = ()=>{};
const cancelGoal = ()=>{};
const T = new Proxy({}, {get:()=>"color"});
const S_FAR=1, S_SIDE=1;
const chip=()=>{}, fillRR=()=>{}, wrap=()=>{};
let texts=[], buttons={};
const txt=(_ctx,text)=>texts.push(text);
const definitions={};
class Panel {constructor(name, options){definitions[name]=options;}}
const ctx = {save:()=>{},restore:()=>{},beginPath:()=>{},rect:()=>{},clip:()=>{},measureText:()=>({width:10})};
const panel = {fs:x=>x, root:()=>{}, button:(id,x,y,w,h,fn)=>{buttons[id]=fn;return false;}};
let calls=[];
global.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({task:{mode:"find",state:"running",target:"ball",steps:[]}})};};
''' + presets + handlers + start_goal + helpers + panels + '\n(async()=>{\n' + checks + '\n})().catch(e=>{console.error(e);process.exit(1)});'
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_find_presets_send_object_to_find_endpoint():
    _run_find_ui_js(r'''
for (const object of ["ball","person","chair","bottle"]) {
  const preset=LLM_PRESETS.find(([label])=>label===`Find a ${object}`);
  assert.ok(preset, object);
  await startGoal(preset[1]);
  const call=calls.at(-1);
  assert.equal(call.url,"/api/v1/llm/find");
  assert.equal(call.options.method,"POST");
  assert.deepEqual(JSON.parse(call.options.body),{object});
}
''')


def test_find_auto_bar_and_desktop_button_only_show_during_running_find():
    _run_find_ui_js(r'''
for (const mode of [undefined,"plan","find"]) {
  for (const stateName of ["idle","running","done","cancelled","failed"]) {
    state.llmTask={mode,state:stateName,steps:[]};
    definitions.auto.draw({},panel);
    syncFinding();
    const expected=mode==="find" && stateName==="running";
    assert.equal(panel.visible,expected,`${mode}/${stateName}`);
    assert.equal(desktop.hidden,!expected);
  }
}
''')


def test_stop_finding_controls_use_find_stop_endpoint():
    _run_find_ui_js(r'''
state.llmTask={mode:"find",state:"running",steps:[]};
definitions.auto.draw({},panel);
await buttons.stopFinding();
assert.equal(calls.at(-1).url,"/api/v1/llm/find/stop");
assert.equal(calls.at(-1).options.method,"POST");
''')
    html = TestClient(app).get("/cockpit/vr").text
    assert 'document.getElementById("stop-finding").addEventListener("click", stopFinding)' in html


def test_brain_cancel_switches_label_and_handler_for_find_tasks():
    _run_find_ui_js(r'''
for (const mode of ["plan","find"]) {
  texts=[];buttons={};state.llmTask={mode,state:"running",steps:[]};
  definitions.brain.draw(ctx,panel);
  assert.ok(texts.includes(mode==="find" ? "Stop finding" : "Cancel"));
  assert.equal(buttons.cancel,mode==="find" ? stopFinding : cancelGoal);
}
''')


def test_brain_answer_scroll_behavior_and_controller_motion_isolation():
    """Run shipped canvas/scroll/controller code against Node test doubles."""
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for cockpit behavior coverage")
    html = TestClient(app).get("/cockpit/vr").text
    helpers = html[html.index("function scrollBrain(lines)"):html.index('new Panel("brain",')]
    brain = html[html.index('new Panel("brain",'):html.index('new Panel("auto",')]
    controllers = html[html.index("function updateControllers(now, dt)"):html.index("// ------------------------------------------------------------------ desktop:")]
    script = r'''
const assert=require('node:assert/strict');
const state={brainOpen:true,llmTask:{id:'one',state:'done',answer:Array.from({length:30},(_,i)=>`Line ${i+1}`).join('\n'),steps:[{tool:'turn',args:{degrees:30}}]}};
const DEAD_ZONE=.12,VOICE_HOLD_S=.35,ESTOP_HOLD_S=.3;
const markDirty=()=>{},setFont=()=>{},chip=()=>{},fillRR=()=>{},wrap=()=>{};
const T=new Proxy({},{get:()=>''}),LLM_PRESETS=[];
const startGoal=()=>{},cancelGoal=()=>{},stopFinding=()=>{};
let definition,texts=[],buttons={},clipped=false,clipHeight=0;
class Panel{constructor(name,options){definition=options;}}
const ctx={save:()=>{},restore:()=>{},beginPath:()=>{},rect:(x,y,w,h)=>{clipHeight=h;},clip:()=>{clipped=true;},measureText:t=>({width:t.length*10})};
const txt=(ctx,text)=>texts.push(text);
const p={fs:x=>x,root:()=>{},button:(id,x,y,w,h,fn,enabled=true)=>{buttons[id]={fn,enabled};return false;}};
''' + helpers + brain + r'''
definition.draw(ctx,p);
assert.equal(clipped,true);assert.equal(buttons.answerUp.enabled,false);
assert.ok(texts.includes('line 1–9 of 30'));assert.ok(!texts.includes('turn 30°'));
const expanded=clipHeight;buttons.answerDown.fn();assert.equal(state.brainScroll,1);
texts=[];definition.draw(ctx,p);assert.ok(texts.includes('line 2–10 of 30'));
state.llmTask.state='running';definition.draw(ctx,p);assert.ok(clipHeight<expanded);
state.llmTask.answer+='\nNew answer';definition.draw(ctx,p);assert.equal(state.brainScroll,0);
scrollBrain(3);state.llmTask.id='two';definition.draw(ctx,p);assert.equal(state.brainScroll,0);
scrollBrain(1000);definition.draw(ctx,p);assert.equal(buttons.answerDown.enabled,false);
state.brainScroll=0;state.brainScrollMax=20;
const stick={};scrollBrainStick(stick,1,true,1000);assert.equal(state.brainScroll,1);
scrollBrainStick(stick,1,true,1100);assert.equal(state.brainScroll,1);
scrollBrainStick(stick,1,true,1150);assert.equal(state.brainScroll,2);
scrollBrainStick(stick,1,false,1300);assert.equal(state.brainScroll,2);
const vec={copy(){return this},set(){return this},applyMatrix4(){return this},setFromMatrixPosition(){return this}};
const tmpMat={identity(){return this},extractRotation(){return this}},tmpOrigin=vec,tmpDir=vec,tmpQuat={};
const panels={brain:{}},pickPanel=()=>panels.brain,pick=()=>null,setHover=()=>{};
const controllers=[{hand:'right',source:{gamepad:{axes:[0,0,0,1],buttons:[]}},target:{matrixWorld:{}},cursor:{},ray:{userData:{setLength:()=>{}}},prev:{}}];
const stopRobot=()=>{},toggleActions=()=>{},cycleIntensity=()=>{},startTalking=()=>{},stopTalking=()=>{},triggerEmergencyStop=()=>{},updateGrab=()=>{},resetLayout=()=>{};
let estopHeld=0,resetHeld=0,sent;
const radial=(x,y)=>[x,y],r2=x=>x,teleopTick=(now,motion)=>{sent=motion};
''' + controllers + r'''
updateControllers(2000,.016);
assert.equal(Math.abs(sent.forward),0);assert.equal(Math.abs(sent.turn),0);assert.equal(Math.abs(sent.strafe),0);
'''
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_body_height_buttons_sequence_commands_and_follow_commanded_state():
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for cockpit behavior tests")
    html = TestClient(app).get("/cockpit/vr").text
    http = html[html.index("let controlSessionId ="):html.index("// ------------------------------------------------------------------ teleop:")]
    handlers = html[html.index("function applyBodyHeight(payload)"):html.index("const cycleIntensity =")]
    panel = html[html.index('new Panel("bodyHeight",'):html.index('new Panel("strip",')]
    polling = html[html.index("async function pollStatus()"):html.index("async function pollTelemetry()")]
    telemetry = html[html.index("function applyTelemetry(t)"):html.index("function connectSocket()")]
    script = r'''
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const LEVELS=['LOW','MEDIUM','HIGH'],S_SIDE=1;
const state={bodyHeight:'MEDIUM',heightPending:false};
const markDirty=()=>{},latchEstop=()=>{};
const T={accent:'accent',neutralFill:'neutral',neutralBorder:'border',grey3D:'grey'};
let definition,buttons={},fills=[],calls=[],refuse=false,commanded=null;
class Panel{constructor(name,options){definition=options;}}
const txt=()=>{},fillRR=(ctx,x,y,w,h,r,fill)=>fills.push(fill);
const p={root:()=>{},fs:x=>x,button:(id,x,y,w,h,fn,enabled)=>{buttons[id]={fn,enabled};return false;}};
global.fetch=async(url,options)=>{
 calls.push({url,options});
 if(url==='/api/v1/session')return {ok:true,json:async()=>({session_id:'simulation'})};
 if(url==='/api/v1/status')return {ok:true,json:async()=>({control_state:{posture:{height:'medium'}}})};
 return {ok:!refuse,status:refuse?400:200,json:async()=>refuse?{detail:'INVALID: unsafe posture'}:commanded?{control_state:{posture:{height:commanded}}}:{status:'accepted'}};
};
''' + http + handlers + panel + polling + telemetry + r'''
(async()=>{
 for(const level of LEVELS){
  definition.draw({},p);await buttons[`height${level}`].fn();
  const call=calls.at(-1);assert.equal(call.url,'/api/v1/posture');assert.equal(call.options.method,'POST');
  assert.deepEqual(JSON.parse(call.options.body),{height:level.toLowerCase(),pitch:0});
  assert.equal(call.options.headers['X-Control-Session'],'simulation');assert.ok(Number(call.options.headers['X-Command-Seq'])>0);
  assert.equal(state.bodyHeight,level);
  fills=[];definition.draw({},p);assert.equal(fills.filter(x=>x==='accent').length,1);assert.equal(fills[LEVELS.indexOf(level)],'accent');
 }
 refuse=true;await setBodyHeight('LOW');assert.equal(state.bodyHeight,'HIGH');
 assert.deepEqual(state.result,{status:'INVALID',message:'unsafe posture'});
 refuse=false;commanded='low';await setBodyHeight('HIGH');assert.equal(state.bodyHeight,'LOW');
 applyTelemetry({control_state:{posture:{height:'high'}}});assert.equal(state.bodyHeight,'HIGH');
 applyTelemetry({});assert.equal(state.bodyHeight,'HIGH');
 applyBodyHeight({control_state:{posture:{height:'invalid'}}});assert.equal(state.bodyHeight,'HIGH');
 await pollStatus();assert.equal(state.bodyHeight,'MEDIUM');
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    # Placement precedes the common registration that supplies moveRoot and saved layout.
    placement = html.index('placeOnArc(P.bodyHeight.group, -SIDE_PANEL_YAW, 1.3, EYE - 0.51)')
    registration = html.index('for (const p of Object.values(panels)) { scene.add(p.group); p.moveRoot = p.group; }')
    assert placement < registration < html.index('const movableRoots =')


def test_vr_haptic_feedback_triggers_on_actions_estop_and_state_events():
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for cockpit behavior tests")
    html = TestClient(app).get("/cockpit/vr").text
    haptics_block = html[html.index("// ------------------------------------------------------------------ haptics"):html.index("async function setIntensity(level)")]
    script = r'''
const assert = require('node:assert/strict');
const pulses = [];
class FakeActuator {
  pulse(intensity, duration) {
    pulses.push({ intensity, duration });
  }
}
const controllerA = {
  source: { gamepad: { hapticActuators: [new FakeActuator()] } }
};
const controllerB = {
  source: { gamepad: { hapticActuators: [new FakeActuator()] } }
};
const controllers = [controllerA, controllerB];
let motionGeneration = 0, pendingMotion = null;
const state = { sent: {}, teleop: 'IDLE', needCentre: false, estopLatched: false, latchPhase: 'idle', latchError: '' };
const markDirty = () => {};
const api = async () => ({ ok: true });
const showResult = () => {};
''' + haptics_block + r'''
// Test direct pulseHaptic
pulseHaptic(controllerA, 0.4, 25);
assert.equal(pulses.length, 1);
assert.equal(pulses[0].intensity, 0.4);
assert.equal(pulses[0].duration, 25);

// Test pulseAllHaptics
pulses.length = 0;
pulseAllHaptics(0.7, 100);
assert.equal(pulses.length, 2);
assert.equal(pulses[0].intensity, 0.7);
assert.equal(pulses[1].intensity, 0.7);

// Test stopRobot triggers haptics
pulses.length = 0;
stopRobot('Operator stop');
assert.equal(pulses.length, 2);
assert.equal(pulses[0].intensity, 0.6);
assert.equal(pulses[0].duration, 60);

// Test latchEstop triggers full intensity emergency haptics
pulses.length = 0;
latchEstop();
assert.equal(state.estopLatched, true);
assert.equal(pulses.length, 2);
assert.equal(pulses[0].intensity, 1.0);
assert.equal(pulses[0].duration, 300);
'''
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
