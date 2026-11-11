"""Phase 6 LLM planner prototype: tool validation, planners (Claude mocked), executor, router."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.control.executor import DryRunGateway, TaskRunner
from backend.ai.control.planner import (
    DEFAULT_MODEL,
    AnthropicPlanner,
    MockPlanner,
    PlannerError,
    PlannerNotConfigured,
)
from backend.ai.control.router import create_llm_router
from backend.ai.control.tools import (
    MAX_PLAN_STEPS,
    TOOL_DEFS,
    PlanValidationError,
    ToolCall,
    validate_call,
    validate_plan,
)
from backend.robot import protocol
from backend.robot.relay_router import QuestRelayAuth

KEY = "quest-secret"
MOVE = {"direction": "forward", "seconds": 2, "speed": 0.3}


# ------------------------------------------------------------------ tools / validation

def test_tool_schemas_are_strict_and_closed():
    assert {t["name"] for t in TOOL_DEFS} == {
        "move", "turn", "stop", "set_style", "run_action", "get_status", "get_detections"}
    for tool in TOOL_DEFS:
        schema = tool["input_schema"]
        assert tool["strict"] is True
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])


@pytest.mark.parametrize("name, args", [
    ("move", MOVE),
    ("move", {"direction": "left", "seconds": 0.2, "speed": 0}),
    ("move", {"direction": "backward", "seconds": 5, "speed": 0.6}),
    ("turn", {"degrees": -180}), ("turn", {"degrees": 180}), ("turn", {"degrees": 30.5}),
    ("stop", {}), ("get_status", {}), ("get_detections", {}),
    ("set_style", {"intensity": "low", "gait": "walk"}),
    ("run_action", {"action_id": 10}),
])
def test_valid_calls(name, args):
    call = validate_call(name, args)
    assert call.name == name and set(call.args) == set(args)


@pytest.mark.parametrize("name, args", [
    ("move", {**MOVE, "seconds": 5.1}),
    ("move", {**MOVE, "seconds": 0.1}),
    ("move", {**MOVE, "speed": 0.7}),
    ("move", {**MOVE, "speed": -0.1}),
    ("move", {**MOVE, "direction": "up"}),
    ("move", {**MOVE, "seconds": True}),
    ("move", {**MOVE, "seconds": "2"}),
    ("move", {**MOVE, "seconds": float("nan")}),
    ("move", {"direction": "forward", "seconds": 2}),          # missing arg
    ("move", {**MOVE, "extra": 1}),                            # extra arg
    ("turn", {"degrees": 181}), ("turn", {"degrees": -181}),
    ("set_style", {"intensity": "ludicrous", "gait": "trot"}),
    ("set_style", {"intensity": "low", "gait": "moonwalk"}),
    ("run_action", {"action_id": 99}),                         # not in the Pi allowlist
    ("run_action", {"action_id": 255}),                        # reset is not allowed
    ("run_action", {"action_id": "10"}),
    ("stop", {"now": True}),
    ("self_destruct", {}),
])
def test_invalid_calls_rejected(name, args):
    with pytest.raises(PlanValidationError):
        validate_call(name, args)


def test_one_bad_call_rejects_the_whole_plan():
    with pytest.raises(PlanValidationError):
        validate_plan([("move", MOVE), ("turn", {"degrees": 999}), ("stop", {})])


def test_oversized_plan_rejected():
    with pytest.raises(PlanValidationError):
        validate_plan([("stop", {})] * (MAX_PLAN_STEPS + 1))


# ------------------------------------------------------------------ mock planner

@pytest.mark.asyncio
async def test_mock_planner_goal_to_ordered_plan():
    r = await MockPlanner().plan("walk forward two seconds, turn right 45 degrees and report what you see", {}, [])
    assert r.status == "plan"
    assert [c.name for c in r.plan] == ["move", "turn", "get_detections"]
    assert r.plan[0].args["seconds"] == 2 and r.plan[1].args["degrees"] == 45


@pytest.mark.asyncio
async def test_mock_planner_question_returns_text_without_plan():
    r = await MockPlanner().plan("how are you today?", {}, [])
    assert r.status == "answer" and r.plan == [] and r.text


@pytest.mark.asyncio
async def test_mock_planner_out_of_range_rejected():
    with pytest.raises(PlanValidationError):
        await MockPlanner().plan("forward 50 seconds", {}, [])


# ------------------------------------------------------------------ Anthropic planner (client mocked)

def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


class FakeBeta:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.response


def response(content, stop_reason="tool_use", category=None):
    return SimpleNamespace(content=content, stop_reason=stop_reason,
                           stop_details=SimpleNamespace(category=category) if category else None)


def planner_with(resp=None, exc=None, **kw):
    fake = FakeBeta(resp, exc)
    return AnthropicPlanner(client=SimpleNamespace(beta=fake), **kw), fake


@pytest.mark.asyncio
async def test_anthropic_tool_use_blocks_become_validated_plan():
    p, fake = planner_with(response([
        block("thinking", thinking=""),
        block("tool_use", id="1", name="move", input=MOVE),
        block("tool_use", id="2", name="turn", input={"degrees": 90}),
    ]))
    r = await p.plan("forward then turn", {"state": "idle"}, [])
    assert r.status == "plan" and [c.name for c in r.plan] == ["move", "turn"]
    call = fake.calls[0]
    assert call["model"] == DEFAULT_MODEL == "claude-sonnet-5-5"
    assert call["tool_choice"] == {"type": "auto"}          # forced tool_choice 400s on this model
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    assert call["output_config"] == {"effort": "low"}
    assert call["max_tokens"] == 8000                        # room for adaptive-thinking tokens
    assert "thinking" not in call                            # disabled thinking 400s on this model
    assert call["tools"] is not None and all(t["strict"] for t in call["tools"])
    assert "idle" in call["messages"][0]["content"] and "forward then turn" in call["messages"][0]["content"]


@pytest.mark.asyncio
async def test_anthropic_text_only_is_an_answer_not_a_plan():
    p, _ = planner_with(response([block("text", text="I see a chair.")], "end_turn"))
    r = await p.plan("what is that?", {}, [])
    assert (r.status, r.plan, r.text) == ("answer", [], "I see a chair.")


@pytest.mark.asyncio
async def test_anthropic_refusal_is_returned_not_executed():
    p, _ = planner_with(response([block("tool_use", id="1", name="move", input=MOVE)], "refusal", "general_harms"))
    r = await p.plan("do something bad", {}, [])
    assert r.status == "refused" and r.plan == []


@pytest.mark.asyncio
async def test_anthropic_out_of_range_call_rejects_plan():
    p, _ = planner_with(response([block("tool_use", id="1", name="move", input={**MOVE, "seconds": 60})]))
    with pytest.raises(PlanValidationError):
        await p.plan("go", {}, [])


@pytest.mark.asyncio
async def test_anthropic_truncated_output_is_an_error():
    p, _ = planner_with(response([block("tool_use", id="1", name="move", input=MOVE)], "max_tokens"))
    with pytest.raises(PlannerError):
        await p.plan("go", {}, [])


@pytest.mark.asyncio
async def test_anthropic_request_failure_is_wrapped_without_details():
    p, _ = planner_with(exc=RuntimeError("sk-ant-secret"))
    with pytest.raises(PlannerError) as e:
        await p.plan("go", {}, [])
    assert "sk-ant" not in str(e.value)


@pytest.mark.asyncio
async def test_missing_api_key_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(PlannerNotConfigured):
        await AnthropicPlanner().plan("go", {}, [])


@pytest.mark.asyncio
async def test_model_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_CONTROL_MODEL", "claude-test-model")
    p, fake = planner_with(response([block("text", text="hi")], "end_turn"))
    await p.plan("hi", {}, [])
    assert fake.calls[0]["model"] == "claude-test-model"


# ------------------------------------------------------------------ executor

def calls(plan):
    return [ToolCall(n, a) for n, a in plan]


@pytest.mark.asyncio
async def test_executor_runs_every_step_as_source_llm():
    g = DryRunGateway(time_scale=0)
    runner = TaskRunner(g)
    await runner.start("goal", calls([("move", MOVE), ("turn", {"degrees": 90}), ("get_status", {}),
                                      ("set_style", {"intensity": "low", "gait": "walk"}),
                                      ("run_action", {"action_id": 10}), ("stop", {})]))
    await runner.wait()
    assert runner.task.state == "done" and all(s.state == "done" for s in runner.task.steps)
    names = [n for n, _ in g.calls]
    assert names == ["run_bounded", "turn", "set_style", "run_action", "stop"]
    assert all(kw["source"] == "llm" for _, kw in g.calls)


@pytest.mark.asyncio
async def test_cancel_mid_plan_stops_and_calls_stop():
    g = DryRunGateway(time_scale=1)
    runner = TaskRunner(g)
    await runner.start("goal", calls([("move", {**MOVE, "seconds": 5}), ("turn", {"degrees": 90})]))
    await asyncio.sleep(0.05)
    assert runner.task.state == "running" and runner.task.steps[0].state == "running"
    assert await runner.cancel() is True
    assert runner.task.state == "cancelled"
    assert [s.state for s in runner.task.steps] == ["skipped", "skipped"]
    assert g.calls[-1] == ("stop", {"source": "llm"})
    assert [n for n, _ in g.calls] == ["run_bounded", "stop"]   # the turn never ran
    assert await runner.cancel() is False                        # nothing left to cancel


@pytest.mark.asyncio
async def test_step_failure_stops_and_marks_failed():
    class Boom(DryRunGateway):
        async def turn(self, degrees, *, source):
            raise RuntimeError("gateway down")

    g = Boom(time_scale=0)
    runner = TaskRunner(g)
    await runner.start("goal", calls([("move", MOVE), ("turn", {"degrees": 5}), ("move", MOVE)]))
    await runner.wait()
    assert runner.task.state == "failed" and runner.task.steps[1].state == "failed"
    assert [n for n, _ in g.calls] == ["run_bounded", "stop"]


@pytest.mark.asyncio
async def test_new_task_cancels_the_running_one():
    g = DryRunGateway(time_scale=1)
    runner = TaskRunner(g)
    first = await runner.start("a", calls([("move", {**MOVE, "seconds": 5})]))
    await asyncio.sleep(0.02)
    second = await runner.start("b", calls([("stop", {})]))
    assert first.state == "cancelled" and second.id == first.id + 1
    await runner.wait()
    assert [n for n, _ in g.calls][:2] == ["run_bounded", "stop"]


# ------------------------------------------------------------------ router

def make_client(planner=None, gateway=None, key=KEY):
    app = FastAPI()
    app.include_router(create_llm_router(QuestRelayAuth(key), planner or MockPlanner(), gateway))
    return TestClient(app)


AUTH = {protocol.AUTH_HEADER: KEY}


def test_plan_starts_immediately_without_confirm_and_is_visible():
    g = DryRunGateway(time_scale=1)
    with make_client(gateway=g) as c:
        r = c.post("/api/v1/llm/plan", json={"goal": "forward 3 seconds then turn left"}, headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "started" and body["plan"] == ["move", "turn"]
        assert body["task"]["state"] == "running"
        task = c.get("/api/v1/llm/task", headers=AUTH).json()
        assert task["state"] == "running" and task["goal"] == "forward 3 seconds then turn left"
        assert g.calls and g.calls[0][0] == "run_bounded"       # started with no confirm call


def test_cancel_endpoint_stops_the_task():
    g = DryRunGateway(time_scale=1)
    with make_client(gateway=g) as c:
        c.post("/api/v1/llm/plan", json={"goal": "forward 5 seconds then turn right"}, headers=AUTH)
        r = c.post("/api/v1/llm/task/cancel", headers=AUTH).json()
        assert r["cancelled"] is True and r["task"]["state"] == "cancelled"
        assert g.calls[-1][0] == "stop"
        assert c.post("/api/v1/llm/task/cancel", headers=AUTH).json()["cancelled"] is False


def test_idle_task():
    with make_client() as c:
        assert c.get("/api/v1/llm/task", headers=AUTH).json() == {"state": "idle"}


def test_question_returns_text_and_no_plan():
    g = DryRunGateway(time_scale=0)
    with make_client(gateway=g) as c:
        body = c.post("/api/v1/llm/plan", json={"goal": "who are you?"}, headers=AUTH).json()
    assert body["status"] == "answer" and body["text"] and body["plan"] == [] and g.calls == []


def test_out_of_range_plan_is_422_and_nothing_runs():
    g = DryRunGateway(time_scale=0)
    with make_client(gateway=g) as c:
        r = c.post("/api/v1/llm/plan", json={"goal": "forward 50 seconds"}, headers=AUTH)
    assert r.status_code == 422 and g.calls == []


def test_refused_plan_is_reported_and_not_run():
    p, _ = planner_with(response([block("tool_use", id="1", name="move", input=MOVE)], "refusal", "cyber"))
    g = DryRunGateway(time_scale=0)
    with make_client(planner=p, gateway=g) as c:
        body = c.post("/api/v1/llm/plan", json={"goal": "x"}, headers=AUTH).json()
    assert body["status"] == "refused" and g.calls == []


def test_auth_required_and_fails_closed():
    with make_client() as c:
        for path, method in (("/api/v1/llm/plan", "post"), ("/api/v1/llm/task", "get"), ("/api/v1/llm/task/cancel", "post")):
            kw = {"json": {"goal": "stop"}} if path.endswith("plan") else {}
            assert getattr(c, method)(path, **kw).status_code == 401
            assert getattr(c, method)(path, headers={protocol.AUTH_HEADER: "no"}, **kw).status_code == 401
    with make_client(key=None) as c:
        assert c.get("/api/v1/llm/task", headers=AUTH).status_code == 503


def test_missing_anthropic_key_is_503(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with make_client(planner=AnthropicPlanner()) as c:
        assert c.post("/api/v1/llm/plan", json={"goal": "forward"}, headers=AUTH).status_code == 503


def test_planner_failure_is_502_without_leaking():
    p, _ = planner_with(exc=RuntimeError("sk-ant-secret"))
    with make_client(planner=p) as c:
        r = c.post("/api/v1/llm/plan", json={"goal": "forward"}, headers=AUTH)
    assert r.status_code == 502 and "sk-ant" not in r.text


def test_goal_validation():
    with make_client() as c:
        assert c.post("/api/v1/llm/plan", json={"goal": ""}, headers=AUTH).status_code == 422
        assert c.post("/api/v1/llm/plan", json={"goal": "x" * 501}, headers=AUTH).status_code == 422


def test_app_registers_llm_routes(monkeypatch):
    monkeypatch.setenv("QUEST_API_KEY", KEY)
    from backend.main import app

    paths = app.openapi()["paths"]
    assert {"/api/v1/llm/plan", "/api/v1/llm/task/cancel", "/api/v1/llm/task"} <= set(paths)
