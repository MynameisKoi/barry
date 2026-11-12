"""``/api/v1/llm/*``: give the robot brain a goal, watch the task, cancel it.

``POST /plan`` keeps its name (the shape is unchanged) but now starts the brain loop and returns
at once; poll ``GET /task`` for the steps and the final ``answer``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.ai.control.brain import Brain, BrainNotConfigured, create_brain
from backend.ai.control.executor import (
    DryRunGateway, FindLimits, FindUnsupported, GatewayLike, LoopLimits, TaskRunner, valid_find_object,
)
from backend.ai.control.gateway_adapter import CommandGatewayAdapter, _shared_gateway
from backend.robot.relay_router import QuestRelayAuth

logger = logging.getLogger(__name__)


class PlanRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=500)


class FindRequest(BaseModel):
    object: str = Field(min_length=1, max_length=40, description="what to find, e.g. 'ball' or 'chair' (letters, spaces, - and ')")


def create_llm_router(
    auth: QuestRelayAuth | None = None,
    brain: Brain | None = None,
    gateway: GatewayLike | None = None,
    limits: LoopLimits | None = None,
    runner: TaskRunner | None = None,
    camera: Any = None,
    run_logger: Any = None,
) -> APIRouter:
    if runner is None:
        if gateway is None and os.environ.get("LLM_GATEWAY", "real").lower() == "dry":
            gateway = DryRunGateway()
        elif gateway is None:
            gateway = CommandGatewayAdapter(_shared_gateway)
        runner = TaskRunner(gateway, brain or create_brain(), limits or LoopLimits.from_env(), camera=camera,
                            find_limits=FindLimits.from_env(), run_logger=run_logger)
    preempt_hook = getattr(gateway, "on_preempt", None)
    if callable(preempt_hook):
        preempt_hook(lambda _source: runner.request_cancel())
    router = APIRouter(
        prefix="/api/v1/llm",
        tags=["LLM"],
        dependencies=[Depends(auth or QuestRelayAuth.from_env())],
    )

    @router.post("/plan")
    async def start_task(body: PlanRequest) -> dict[str, Any]:
        goal = body.goal.strip()
        logger.info("LLM goal: %r", goal)
        try:
            # No confirm step: the loop starts now; the gateway's limits are the safety net.
            task = await runner.start(goal)
        except BrainNotConfigured:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM control is not configured")
        return {"status": "started", "task": task.to_dict()}

    @router.post("/find")
    async def start_find(body: FindRequest) -> dict[str, Any]:
        """Autonomous find-an-object session: search (look/turn/step, <= 120 s each) -> WATCH -> automatic re-search when
        the object is lost, until LLM_FIND_SESSION_S. Starts at once; poll GET /task."""
        target = body.object.strip().lower()
        if not valid_find_object(target):   # raw text: no newlines or control characters, at most 3 words
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "object must be 1-3 words of letters (spaces, - and ' allowed)")
        logger.info("LLM find: %r", target)
        try:
            task = await runner.start_find(target)
        except BrainNotConfigured:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM control is not configured")
        except FindUnsupported:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "find mode is not available with this brain")
        return {"status": "started", "task": task.to_dict()}

    @router.post("/find/stop")
    async def stop_find() -> dict[str, Any]:
        """The "Stop finding" button: end the find session (search or watch), stop the robot, reset the posture to
        level/medium. Does nothing to a non-find task (use /task/cancel for those)."""
        task = runner.task
        if task is None or task.state != "running" or task.mode != "find":
            return {"stopped": False, "task": runner.snapshot()}
        stopped = await runner.cancel()
        return {"stopped": stopped, "task": runner.snapshot()}

    @router.post("/task/cancel")
    async def cancel_task() -> dict[str, Any]:
        cancelled = await runner.cancel()
        return {"cancelled": cancelled, "task": runner.snapshot()}

    @router.get("/task")
    async def get_task() -> dict[str, Any]:
        return runner.snapshot()

    router.task_runner = runner
    return router
