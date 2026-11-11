"""Phase 6b LLM brain loop: tools, Anthropic brain (client mocked), loop limits/cancel, router."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.control.brain import (
    DEFAULT_MODEL,
    AnthropicBrain,
    BrainError,
    BrainNotConfigured,
    MockBrain,
    ModelTurn,
    RawCall,
)
from backend.ai.control.executor import DryRunGateway, LoopLimits, TaskRunner
from backend.ai.control.router import create_llm_router
from backend.ai.control.tools import TOOL_DEFS, PlanValidationError, validate_call
from backend.robot import protocol
from backend.robot.relay_router import QuestRelayAuth

KEY = "quest-secret"
MOVE = {"direction": "forward", "seconds": 2, "speed": 0.3}
CHAIR = [{"label": "chair", "confidence": 0.9}]


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
    ("move", {**MOVE, "seconds": 5.1}), ("move", {**MOVE, "seconds": 0.1}),
    ("move", {**MOVE, "speed": 0.7}), ("move", {**MOVE, "speed": -0.1}),
    ("move", {**MOVE, "direction": "up"}), ("move", {**MOVE, "seconds": True}),
    ("move", {**MOVE, "seconds": "2"}), ("move", {**MOVE, "seconds": float("nan")}),
    ("move", {"direction": "forward", "seconds": 2}), ("move", {**MOVE, "extra": 1}),
    ("turn", {"degrees": 181}), ("turn", {"degrees": -181}),
    ("set_style", {"intensity": "ludicrous", "gait": "trot"}),
    ("set_style", {"intensity": "low", "gait": "moonwalk"}),
    ("run_action", {"action_id": 99}), ("run_action", {"action_id": 255}),
    ("run_action", {"action_id": "10"}), ("stop", {"now": True}), ("self_destruct", {}),
])
def test_invalid_calls_rejected(name, args):
    with pytest.raises(PlanValidationError):
        validate_call(name, args)


# ------------------------------------------------------------------ scripted models

def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def use(call_id, name, args):
    return block("tool_use", id=call_id, name=name, input=args)


def reply(content, stop_reason=None, category=None):
    stop = stop_reason or ("tool_use" if any(b.type == "tool_use" for b in content) else "end_turn")
    return SimpleNamespace(content=content, stop_reason=stop,
                           stop_details=SimpleNamespace(category=category) if category else None)


class FakeBeta:
    """Mocked ``client.beta.messages``: returns scripted responses, records each request."""

    def __init__(self, responses=(), exc=None):
        self.responses, self.exc, self.calls = list(responses), exc, []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot the history
        if self.exc:
            raise self.exc
        return self.responses.pop(0)


def brain_with(*responses, exc=None, **kw):
    fake = FakeBeta(responses, exc)
    return AnthropicBrain(client=SimpleNamespace(beta=fake), **kw), fake


def make_runner(brain, gateway=None, **limits):
    gateway = gateway or DryRunGateway(time_scale=0, detections_data=CHAIR)
    return TaskRunner(gateway, brain, LoopLimits(**limits)), gateway


async def run(runner, goal="do it"):
    await runner.start(goal)
    await runner.wait()
    return runner.task


def names(gateway):
    return [n for n, _ in gateway.calls]


# ------------------------------------------------------------------ the loop

@pytest.mark.asyncio
async def test_look_and_tell_me_what_you_see_feeds_the_result_back():
    brain, fake = brain_with(
        reply([use("t1", "get_detections", {})]),
        reply([block("text", text="I see a chair ahead.")]),
    )
    runner, gateway = make_runner(brain)
    task = await run(runner, "look and tell me what you see")

    assert task.state == "done" and task.answer == "I see a chair ahead." and task.turns == 2
    second = fake.calls[1]["messages"]
    assert second[-1]["role"] == "user"
    result = second[-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "t1"
    assert "chair" in result["content"] and "is_error" not in result      # the model saw the detections
    assert names(gateway) == []                                          # get_detections is read-only
    assert task.steps[0].tool == "get_detections" and task.steps[0].result == CHAIR


@pytest.mark.asyncio
async def test_invalid_call_returns_is_error_never_executes_then_model_corrects():
    brain, fake = brain_with(
        reply([use("bad", "move", {**MOVE, "seconds": 60})]),
        reply([use("good", "move", {**MOVE, "seconds": 5})]),
        reply([block("text", text="Moved.")]),
    )
    runner, gateway = make_runner(brain)
    task = await run(runner)

    err = fake.calls[1]["messages"][-1]["content"][0]
    assert err["tool_use_id"] == "bad" and err["is_error"] is True
    assert "seconds" in err["content"] and "5.0" in err["content"]      # explains the range
    assert [c for n, c in gateway.calls if n == "run_bounded"] == [
        {"direction": "forward", "seconds": 5.0, "speed": 0.3, "source": "llm"}]  # only the valid one ran
    assert [s.state for s in task.steps] == ["rejected", "done"]
    assert task.state == "done" and task.answer == "Moved."


@pytest.mark.asyncio
async def test_invalid_calls_count_toward_the_step_cap():
    bad = lambda i: reply([use(f"b{i}", "turn", {"degrees": 999})])
    brain, _ = brain_with(*[bad(i) for i in range(5)])
    runner, gateway = make_runner(brain, max_steps=3)
    task = await run(runner)
    assert task.state == "limit" and "step limit" in task.message
    assert len(task.steps) == 3 and names(gateway) == ["stop"]


@pytest.mark.asyncio
async def test_step_cap_stops_and_ends_with_limit():
    brain, fake = brain_with(*[reply([use(f"m{i}", "move", {**MOVE, "seconds": 0.2})]) for i in range(10)])
    runner, gateway = make_runner(brain, max_steps=3)
    task = await run(runner)
    assert task.state == "limit" and "step limit of 3" in task.message
    assert names(gateway) == ["run_bounded"] * 3 + ["stop"]
    assert gateway.calls[-1] == ("stop", {"source": "llm"})


@pytest.mark.asyncio
async def test_turn_cap_stops_and_ends_with_limit():
    brain, fake = brain_with(*[reply([use(f"s{i}", "get_status", {})]) for i in range(10)])
    runner, gateway = make_runner(brain, max_turns=3, max_steps=50)
    task = await run(runner)
    assert task.state == "limit" and "turn limit of 3" in task.message and task.turns == 3
    assert len(fake.calls) == 3 and names(gateway) == ["stop"]


@pytest.mark.asyncio
async def test_timeout_during_a_model_call_ends_with_limit_and_stop():
    class Slow:
        calls = 0
        def ensure_ready(self): pass
        async def turn(self, messages):
            Slow.calls += 1
            await asyncio.sleep(10)

    runner, gateway = make_runner(Slow(), timeout_s=0.05)
    task = await run(runner)
    assert task.state == "limit" and "time limit" in task.message
    assert names(gateway) == ["stop"] and Slow.calls == 1


@pytest.mark.asyncio
async def test_timeout_during_a_move_ends_with_limit_and_stop():
    brain, fake = brain_with(reply([use("m", "move", {**MOVE, "seconds": 5})]), reply([block("text", text="x")]))
    runner, gateway = make_runner(brain, DryRunGateway(time_scale=1), timeout_s=0.1)
    task = await run(runner)
    assert task.state == "limit" and names(gateway) == ["run_bounded", "stop"]
    assert task.steps[0].state == "skipped" and len(fake.calls) == 1     # no model call after the limit


@pytest.mark.asyncio
async def test_cancel_during_a_model_call_stops_and_makes_no_further_call():
    class Hang:
        calls = 0
        def ensure_ready(self): pass
        async def turn(self, messages):
            Hang.calls += 1
            await asyncio.Event().wait()

    runner, gateway = make_runner(Hang())
    await runner.start("goal")
    await asyncio.sleep(0.05)
    assert Hang.calls == 1 and runner.task.state == "running"
    assert await runner.cancel() is True
    await asyncio.sleep(0.05)
    assert runner.task.state == "cancelled" and names(gateway) == ["stop"] and Hang.calls == 1
    assert await runner.cancel() is False


@pytest.mark.asyncio
async def test_cancel_during_a_move_stops_and_makes_no_further_call():
    brain, fake = brain_with(reply([use("m", "move", {**MOVE, "seconds": 5})]), reply([block("text", text="x")]))
    runner, gateway = make_runner(brain, DryRunGateway(time_scale=1))
    await runner.start("goal")
    await asyncio.sleep(0.05)
    assert runner.task.steps[0].state == "running"
    assert await runner.cancel() is True
    await asyncio.sleep(0.05)
    assert runner.task.state == "cancelled" and runner.task.steps[0].state == "skipped"
    assert names(gateway) == ["run_bounded", "stop"] and len(fake.calls) == 1


@pytest.mark.asyncio
async def test_parallel_tool_calls_return_in_one_user_message_in_order():
    brain, fake = brain_with(
        reply([block("text", text="ok"), use("a", "turn", {"degrees": 10}),
               use("b", "get_status", {}), use("c", "move", {**MOVE, "seconds": 0.2})]),
        reply([block("text", text="done")]),
    )
    runner, gateway = make_runner(brain)
    task = await run(runner)
    history = fake.calls[1]["messages"]
    assert [m["role"] for m in history] == ["user", "assistant", "user"]   # one results message
    assert [r["tool_use_id"] for r in history[-1]["content"]] == ["a", "b", "c"]
    assert names(gateway) == ["turn", "run_bounded"] and task.state == "done"


@pytest.mark.asyncio
async def test_history_is_append_only_and_assistant_content_is_verbatim():
    thinking = block("thinking", thinking="", signature="sig")
    first = [thinking, use("t1", "get_status", {})]
    brain, fake = brain_with(reply(first), reply([use("t2", "get_detections", {})]), reply([block("text", text="ok")]))
    runner, _ = make_runner(brain)
    await run(runner)
    h1, h2, h3 = (c["messages"] for c in fake.calls)
    assert h2[: len(h1)] == h1 and h3[: len(h2)] == h2                      # earlier turns never edited
    assert all(a is b for a, b in zip(h1, h2)) and all(a is b for a, b in zip(h2, h3))
    assert h2[1]["content"] is first and h2[1]["content"][0] is thinking    # thinking block passed back as-is


@pytest.mark.asyncio
async def test_refusal_stops_and_runs_nothing_more():
    brain, fake = brain_with(reply([use("m", "move", MOVE)], "refusal", "general_harms"))
    runner, gateway = make_runner(brain)
    task = await run(runner)
    assert task.state == "refused" and names(gateway) == ["stop"] and len(fake.calls) == 1


@pytest.mark.asyncio
async def test_max_tokens_stops_and_fails():
    brain, _ = brain_with(reply([block("text", text="cut")], "max_tokens"))
    runner, gateway = make_runner(brain)
    task = await run(runner)
    assert task.state == "failed" and names(gateway) == ["stop"]


@pytest.mark.asyncio
async def test_model_failure_stops_and_fails_without_leaking():
    brain, _ = brain_with(exc=RuntimeError("sk-ant-secret"))
    runner, gateway = make_runner(brain)
    task = await run(runner)
    assert task.state == "failed" and "sk-ant" not in json.dumps(task.to_dict()) and names(gateway) == ["stop"]


@pytest.mark.asyncio
async def test_gateway_failure_stops_and_fails():
    class Boom(DryRunGateway):
        async def turn(self, degrees, *, source):
            raise RuntimeError("gateway down")

    brain, _ = brain_with(reply([use("a", "turn", {"degrees": 5})]), reply([block("text", text="x")]))
    runner, gateway = make_runner(brain, Boom(time_scale=0))
    task = await run(runner)
    assert task.state == "failed" and task.steps[0].state == "failed" and names(gateway) == ["stop"]


@pytest.mark.asyncio
async def test_every_step_goes_through_the_gateway_as_source_llm():
    calls = [use("1", "move", {**MOVE, "seconds": 0.2}), use("2", "turn", {"degrees": 5}),
             use("3", "set_style", {"intensity": "low", "gait": "walk"}),
             use("4", "run_action", {"action_id": 10}), use("5", "stop", {})]
    brain, _ = brain_with(reply(calls), reply([block("text", text="done")]))
    runner, gateway = make_runner(brain)
    await run(runner)
    assert names(gateway) == ["run_bounded", "turn", "set_style", "run_action", "stop"]
    assert all(kw["source"] == "llm" for _, kw in gateway.calls)


@pytest.mark.asyncio
async def test_new_task_cancels_the_running_one():
    brain, _ = brain_with(reply([use("m", "move", {**MOVE, "seconds": 5})]), reply([block("text", text="second")]))
    runner, gateway = make_runner(brain, DryRunGateway(time_scale=1))
    first = await runner.start("a")
    await asyncio.sleep(0.03)
    second = await runner.start("b")
    assert first.state == "cancelled" and second.id == first.id + 1
    await runner.wait()
    assert names(gateway)[:2] == ["run_bounded", "stop"]


# ------------------------------------------------------------------ Anthropic request shape

@pytest.mark.asyncio
async def test_request_params_follow_sonnet_55_rules():
    brain, fake = brain_with(reply([block("text", text="hi")]))
    runner, _ = make_runner(brain)
    await run(runner, "hello")
    call = fake.calls[0]
    assert call["model"] == DEFAULT_MODEL == "claude-sonnet-5-5"
    assert call["tool_choice"] == {"type": "auto"}          # forced tool_choice 400s on this model
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    assert call["output_config"] == {"effort": "low"} and call["max_tokens"] == 8000
    assert "thinking" not in call and all(t["strict"] for t in call["tools"])
    assert "hello" in call["messages"][0]["content"] and "idle" in call["messages"][0]["content"]


@pytest.mark.asyncio
async def test_model_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_CONTROL_MODEL", "claude-test-model")
    brain, fake = brain_with(reply([block("text", text="hi")]))
    await brain.turn([{"role": "user", "content": "x"}])
    assert fake.calls[0]["model"] == "claude-test-model"


@pytest.mark.asyncio
async def test_missing_api_key_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(BrainNotConfigured):
        AnthropicBrain().ensure_ready()
    with pytest.raises(BrainNotConfigured):
        await AnthropicBrain().turn([])


def test_limits_from_env(monkeypatch):
    monkeypatch.setenv("LLM_MAX_STEPS", "5")
    monkeypatch.setenv("LLM_MAX_TURNS", "abc")
    monkeypatch.setenv("LLM_TIMEOUT_S", "-3")
    assert LoopLimits.from_env() == LoopLimits(5, 8, 60.0)


# ------------------------------------------------------------------ mock brain (offline mode)

@pytest.mark.asyncio
async def test_mock_brain_look_then_answers_with_the_result():
    runner = TaskRunner(DryRunGateway(time_scale=0, detections_data=CHAIR), MockBrain(), LoopLimits())
    task = await run(runner, "walk forward two seconds and tell me what you see")
    assert task.state == "done" and "chair" in task.answer
    assert [s.tool for s in task.steps] == ["move", "get_detections"]


@pytest.mark.asyncio
async def test_mock_brain_corrects_an_out_of_range_call_after_is_error():
    gateway = DryRunGateway(time_scale=0)
    task = await run(TaskRunner(gateway, MockBrain(), LoopLimits()), "forward 50 seconds")
    assert [s.state for s in task.steps] == ["rejected", "done"]
    assert [c["seconds"] for n, c in gateway.calls if n == "run_bounded"] == [5.0]   # clamped, not 50
    assert task.state == "done"


@pytest.mark.asyncio
async def test_mock_brain_question_gets_text_only():
    gateway = DryRunGateway(time_scale=0)
    task = await run(TaskRunner(gateway, MockBrain(), LoopLimits()), "how are you today?")
    assert task.state == "done" and task.answer and task.steps == [] and gateway.calls == []


# ------------------------------------------------------------------ router

AUTH = {protocol.AUTH_HEADER: KEY}


def make_client(brain=None, gateway=None, limits=None, key=KEY):
    app = FastAPI()
    app.include_router(create_llm_router(QuestRelayAuth(key), brain or MockBrain(), gateway, limits))
    return TestClient(app)


def wait_for_state(client, states, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        task = client.get("/api/v1/llm/task", headers=AUTH).json()
        if task["state"] in states:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task never reached {states}: {task}")


def test_goal_starts_immediately_and_final_answer_is_in_get_task():
    gateway = DryRunGateway(time_scale=0, detections_data=CHAIR)
    with make_client(gateway=gateway) as c:
        body = c.post("/api/v1/llm/plan", json={"goal": "look and tell me what you see"}, headers=AUTH).json()
        assert body["status"] == "started"                       # no confirm step
        task = wait_for_state(c, {"done"})
    assert "chair" in task["answer"] and task["steps"][0]["tool"] == "get_detections"


def test_cancel_endpoint_stops_a_running_move():
    gateway = DryRunGateway(time_scale=1)
    with make_client(gateway=gateway) as c:
        c.post("/api/v1/llm/plan", json={"goal": "forward 5 seconds then turn right"}, headers=AUTH)
        wait_for_state(c, {"running"})
        out = c.post("/api/v1/llm/task/cancel", headers=AUTH).json()
        assert out["cancelled"] is True and out["task"]["state"] == "cancelled"
        assert gateway.calls[-1][0] == "stop"
        assert c.post("/api/v1/llm/task/cancel", headers=AUTH).json()["cancelled"] is False


def test_idle_task():
    with make_client() as c:
        assert c.get("/api/v1/llm/task", headers=AUTH).json() == {"state": "idle"}


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
    with make_client(brain=AnthropicBrain()) as c:
        assert c.post("/api/v1/llm/plan", json={"goal": "forward"}, headers=AUTH).status_code == 503


def test_goal_validation():
    with make_client() as c:
        assert c.post("/api/v1/llm/plan", json={"goal": ""}, headers=AUTH).status_code == 422
        assert c.post("/api/v1/llm/plan", json={"goal": "x" * 501}, headers=AUTH).status_code == 422


def test_app_registers_llm_routes(monkeypatch):
    monkeypatch.setenv("QUEST_API_KEY", KEY)
    from backend.main import app

    assert {"/api/v1/llm/plan", "/api/v1/llm/task/cancel", "/api/v1/llm/task"} <= set(app.openapi()["paths"])
