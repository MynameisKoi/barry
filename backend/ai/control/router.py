"""``/api/v1/llm/*``: plan a goal with the LLM, run it immediately, inspect or cancel it."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.ai.control.executor import DryRunGateway, GatewayLike, TaskRunner
from backend.ai.control.planner import (
    Planner,
    PlannerError,
    PlannerNotConfigured,
    create_planner,
)
from backend.ai.control.tools import PlanValidationError
from backend.robot.relay_router import QuestRelayAuth

logger = logging.getLogger(__name__)


class PlanRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=500)


def create_llm_router(
    auth: QuestRelayAuth | None = None,
    planner: Planner | None = None,
    gateway: GatewayLike | None = None,
) -> APIRouter:
    planner = planner or create_planner()
    # TODO(gateway #57): default to the real CommandGateway instead of the dry-run recorder.
    gateway = gateway or DryRunGateway()
    runner = TaskRunner(gateway)
    router = APIRouter(
        prefix="/api/v1/llm",
        tags=["LLM"],
        dependencies=[Depends(auth or QuestRelayAuth.from_env())],
    )

    @router.post("/plan")
    async def plan_and_run(body: PlanRequest) -> dict[str, Any]:
        goal = body.goal.strip()
        logger.info("LLM goal: %r", goal)
        try:
            result = await planner.plan(goal, await gateway.status(), await gateway.detections())
        except PlannerNotConfigured:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM control is not configured")
        except PlanValidationError as exc:
            logger.warning("LLM plan rejected: %s", exc)
            raise HTTPException(422, f"plan rejected: {exc}")
        except PlannerError:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "planner failed")

        if result.status != "plan":
            return {"status": result.status, "text": result.text, "plan": [], "task": runner.snapshot()}
        # No confirm step: the plan starts immediately; the gateway's limits are the safety net.
        task = await runner.start(goal, result.plan)
        logger.info("LLM plan started: %s", [c.name for c in result.plan])
        return {"status": "started", "text": result.text, "plan": [c.name for c in result.plan], "task": task.to_dict()}

    @router.post("/task/cancel")
    async def cancel_task() -> dict[str, Any]:
        cancelled = await runner.cancel()
        return {"cancelled": cancelled, "task": runner.snapshot()}

    @router.get("/task")
    async def get_task() -> dict[str, Any]:
        return runner.snapshot()

    return router
