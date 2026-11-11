"""``/api/v1/llm/*``: give the robot brain a goal, watch the task, cancel it.

``POST /plan`` keeps its name (the shape is unchanged) but now starts the brain loop and returns
at once; poll ``GET /task`` for the steps and the final ``answer``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.ai.control.brain import Brain, BrainNotConfigured, create_brain
from backend.ai.control.executor import DryRunGateway, GatewayLike, LoopLimits, TaskRunner
from backend.robot.relay_router import QuestRelayAuth

logger = logging.getLogger(__name__)


class PlanRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=500)


def create_llm_router(
    auth: QuestRelayAuth | None = None,
    brain: Brain | None = None,
    gateway: GatewayLike | None = None,
    limits: LoopLimits | None = None,
) -> APIRouter:
    # TODO(gateway #57): default to the real CommandGateway instead of the dry-run recorder.
    runner = TaskRunner(gateway or DryRunGateway(), brain or create_brain(), limits or LoopLimits.from_env())
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

    @router.post("/task/cancel")
    async def cancel_task() -> dict[str, Any]:
        cancelled = await runner.cancel()
        return {"cancelled": cancelled, "task": runner.snapshot()}

    @router.get("/task")
    async def get_task() -> dict[str, Any]:
        return runner.snapshot()

    return router
