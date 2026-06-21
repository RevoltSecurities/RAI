"""FastAPI routes for the async subagent system (TUI/external access)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from rai.harness.subagents_v2.models import (
    CreateSubagentRunRequest,
    HITLDecisionRequest,
    SubagentRunResponse,
    SubagentStatusResponse,
    _now,
)
from rai.harness.subagents_v2.service import SubagentService

router = APIRouter(prefix="/internal/subagents", tags=["internal-subagents"])


@router.get("/runs")
async def list_runs(
    parent_run_id: str = "",
    status: str = "",
) -> list[dict[str, Any]]:
    """List all subagent runs, optionally filtered."""
    service = SubagentService.instance()
    all_meta = list(service._task_meta.values())
    if parent_run_id:
        all_meta = [m for m in all_meta if m.get("parent_run_id") == parent_run_id]
    if status:
        all_meta = [m for m in all_meta if m.get("status") == status]
    return all_meta


@router.get("/runs/{task_id}")
async def get_run(task_id: str) -> SubagentStatusResponse:
    """Get status of a specific subagent run."""
    service = SubagentService.instance()
    meta = service.get_task_meta(task_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Task not found")

    return SubagentStatusResponse(
        task_id=task_id,
        status=meta.get("status", "unknown"),
        agent_name=meta.get("agent_name", ""),
        label=meta.get("label"),
        output_preview=meta.get("output_preview", ""),
        created_at=meta.get("created_at", ""),
        last_updated_at=meta.get("created_at", ""),
        interrupt_pending=service.has_pending_hitl(task_id),
    )


@router.post("/runs/{task_id}/cancel")
async def cancel_run(task_id: str) -> dict[str, str]:
    """Cancel a running subagent."""
    service = SubagentService.instance()
    success = await service.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Task not running or not found")
    return {"status": "cancelled", "task_id": task_id}


@router.get("/runs/{task_id}/interrupt")
async def get_interrupt(task_id: str) -> dict[str, Any]:
    """Check if a task has a pending HITL interrupt."""
    service = SubagentService.instance()
    pending = service.has_pending_hitl(task_id)
    meta = service.get_task_meta(task_id)
    return {
        "task_id": task_id,
        "interrupt_pending": pending,
        "status": meta.get("status", "unknown") if meta else "not_found",
    }


@router.post("/runs/{task_id}/interrupt")
async def submit_interrupt(task_id: str, body: HITLDecisionRequest) -> dict[str, str]:
    """Submit a HITL decision for a subagent interrupt."""
    service = SubagentService.instance()
    decision = {
        "type": body.decision_type,
        "message": body.message,
    }
    if body.edits:
        decision["edits"] = body.edits

    resolved = await service.submit_hitl_decision(task_id, decision)
    if not resolved:
        raise HTTPException(status_code=409, detail="No pending interrupt for this task")
    return {"status": "resolved", "task_id": task_id}


@router.get("/runs/{task_id}/stream")
async def stream_events(task_id: str) -> StreamingResponse:
    """SSE stream for a specific subagent's events."""
    service = SubagentService.instance()
    bus = service.get_bus(task_id)
    if bus is None:
        raise HTTPException(status_code=404, detail="No bus for this task")

    async def event_generator():
        async for event_type, data in bus.subscribe():
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
