"""HTTP endpoints for the RAI subagent harness."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from rai.harness.models import InterruptDecisionRequest
from rai.harness.subagents.registry import (
    _SUBAGENT_BUSES,
    _SUBAGENT_GRAPHS,
    _SUBAGENT_HITL,
    _SUBAGENT_LOCK,
    _SUBAGENT_OUTPUTS,
    _SUBAGENT_REGISTRY,
    _SUBAGENT_TASKS,
)

router = APIRouter(tags=["subagents"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_meta(task_id: str) -> dict:
    with _SUBAGENT_LOCK:
        meta = _SUBAGENT_REGISTRY.get(task_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Subagent task '{task_id}' not found")
    return dict(meta)


def _build_decision(body: "InterruptDecisionRequest") -> dict:
    if body.decision == "approve":
        return {"type": "approve"}
    if body.decision == "approve_for_session":
        return {"type": "approve_for_session"}
    if body.decision == "reject":
        d: dict = {"type": "reject"}
        if body.message:
            d["message"] = body.message
        return d
    if body.decision == "respond":
        if not body.message:
            raise HTTPException(status_code=422, detail="'message' is required when decision='respond'")
        return {"type": "respond", "message": body.message}
    # edit
    if body.edited_action is None:
        raise HTTPException(
            status_code=422,
            detail="'edited_action' is required when decision='edit'",
        )
    return {"type": "edit", "edited_action": body.edited_action}


# ── List / inspect ────────────────────────────────────────────────────────────

@router.get("/subagents")
async def list_subagents(
    status: str | None = Query(default=None, description="Filter by status"),
    parent_run_id: str | None = Query(default=None, description="Filter by parent run"),
    pipeline_id: str | None = Query(default=None, description="Filter by pipeline"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List all subagents (active and recently completed)."""
    with _SUBAGENT_LOCK:
        items = list(_SUBAGENT_REGISTRY.values())

    if status:
        items = [i for i in items if i.get("status") == status]
    if parent_run_id:
        items = [i for i in items if i.get("parent_run_id") == parent_run_id]
    if pipeline_id:
        items = [i for i in items if i.get("pipeline_id") == pipeline_id]

    items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return items[offset: offset + limit]


@router.get("/subagents/{task_id}")
async def get_subagent(task_id: str) -> dict[str, Any]:
    """Get subagent metadata and current status."""
    return _get_meta(task_id)


# ── SSE stream ────────────────────────────────────────────────────────────────

@router.get("/subagents/{task_id}/stream")
async def stream_subagent(task_id: str, request: Request) -> StreamingResponse:
    """SSE stream for a subagent: tokens, tool events, HITL, completion.

    Pass 'Last-Event-ID' header to replay missed events after a reconnect.
    """
    with _SUBAGENT_LOCK:
        bus = _SUBAGENT_BUSES.get(task_id)

    if bus is None:
        # Bus not yet created (race) or already cleaned — create lazily
        from rai.harness.sse import RunEventBus
        bus = RunEventBus.create(task_id)

    raw_last_id = (
        request.headers.get("Last-Event-ID")
        or request.headers.get("last-event-id")
    )
    last_event_id: int | None = None
    if raw_last_id is not None:
        try:
            last_event_id = int(raw_last_id)
        except (ValueError, TypeError):
            pass

    async def _gen():
        try:
            async for frame in bus.subscribe(last_event_id=last_event_id):
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            pass

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ── HITL per-subagent ─────────────────────────────────────────────────────────

@router.get("/subagents/{task_id}/interrupt")
async def get_subagent_interrupt(task_id: str) -> dict[str, Any]:
    """Check whether a HITL interrupt is pending for this subagent."""
    _get_meta(task_id)  # 404 if not found
    with _SUBAGENT_LOCK:
        fut = _SUBAGENT_HITL.get(task_id)
        meta = _SUBAGENT_REGISTRY.get(task_id) or {}
        status = meta.get("status", "unknown")
        action_requests = meta.get("action_requests", [])
        interrupt_id = meta.get("interrupt_id", "")

    if fut is None or fut.done():
        return {"task_id": task_id, "pending": False, "status": status}

    return {
        "task_id": task_id,
        "pending": True,
        "status": status,
        "interrupt_id": interrupt_id,
        "action_requests": action_requests,
        "interrupt_url": f"/subagents/{task_id}/interrupt",
    }


@router.post("/subagents/{task_id}/interrupt")
async def submit_subagent_interrupt(
    task_id: str, body: InterruptDecisionRequest
) -> dict[str, Any]:
    """Submit a HITL decision for a subagent, unblocking its executor loop.

    Decision format (same as /threads/{id}/interrupt):
      {"decision": "approve"}
      {"decision": "reject"}
      {"decision": "edit", "edited_action": {"name": "bash", "args": {...}}}
    """
    _get_meta(task_id)  # 404 if not found

    with _SUBAGENT_LOCK:
        fut = _SUBAGENT_HITL.get(task_id)
        status = (_SUBAGENT_REGISTRY.get(task_id) or {}).get("status", "unknown")

    if fut is None:
        # Recovery case: task restored from tasks.db restart as "interrupted" but
        # no live HITL future exists — resume directly from LangGraph checkpoint.
        if status == "interrupted":
            with _SUBAGENT_LOCK:
                has_graph = task_id in _SUBAGENT_GRAPHS
            if not has_graph:
                raise HTTPException(
                    status_code=409,
                    detail="Subagent interrupted but graph unavailable (recovery incomplete)",
                )
            decision = _build_decision(body)
            from rai.harness.subagents.executor import resume_interrupted_subagent_with_decision
            asyncio.create_task(
                resume_interrupted_subagent_with_decision(task_id, decision)
            )
            return {"task_id": task_id, "decision": body.decision, "resolved": True, "recovered": True}

        raise HTTPException(
            status_code=404,
            detail=f"No pending HITL interrupt for subagent '{task_id}'",
        )
    if fut.done():
        raise HTTPException(status_code=409, detail="Interrupt already resolved")

    decision = _build_decision(body)
    fut.set_result(decision)
    return {"task_id": task_id, "decision": body.decision, "resolved": True}


# ── Cancel ────────────────────────────────────────────────────────────────────

@router.post("/subagents/{task_id}/cancel")
async def cancel_subagent(task_id: str) -> dict[str, Any]:
    """Cancel a running subagent."""
    _get_meta(task_id)  # 404 if not found

    with _SUBAGENT_LOCK:
        task = _SUBAGENT_TASKS.get(task_id)
        status = (_SUBAGENT_REGISTRY.get(task_id) or {}).get("status", "unknown")

    if status in ("completed", "failed", "cancelled", "timeout"):
        return {"task_id": task_id, "status": status, "cancelled": False}

    if task and not task.done():
        task.cancel()

    with _SUBAGENT_LOCK:
        if task_id in _SUBAGENT_REGISTRY:
            _SUBAGENT_REGISTRY[task_id]["status"] = "cancelled"

    return {"task_id": task_id, "status": "cancelled", "cancelled": True}


# ── Output (blocking) ─────────────────────────────────────────────────────────

@router.get("/subagents/{task_id}/output")
async def get_subagent_output(
    task_id: str,
    timeout: float = Query(default=120.0, ge=1.0, le=3600.0),
) -> dict[str, Any]:
    """Block until the subagent finishes and return its output.

    Returns immediately if the subagent is already completed.
    """
    meta = _get_meta(task_id)

    # Already done — return output from registry
    if meta.get("status") in ("completed", "turn_complete", "failed", "cancelled", "timeout"):
        return {
            "task_id": task_id,
            "status": meta["status"],
            "output": meta.get("output") or "",
            "output_file": meta.get("output_file") or "",
        }

    # Wait on output queue
    with _SUBAGENT_LOCK:
        out_q = _SUBAGENT_OUTPUTS.get(task_id)

    if out_q is None:
        raise HTTPException(
            status_code=409,
            detail=f"Subagent '{task_id}' output queue not available",
        )

    try:
        output = await asyncio.wait_for(out_q.get(), timeout=timeout)
        final_meta = _get_meta(task_id)
        return {
            "task_id": task_id,
            "status": final_meta.get("status", "completed"),
            "output": output,
            "output_file": final_meta.get("output_file") or "",
        }
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408,
            detail=f"Subagent '{task_id}' did not complete within {timeout}s",
        )


# ── Thread-scoped list ────────────────────────────────────────────────────────

@router.get("/threads/{thread_id}/subagents")
async def list_thread_subagents(
    thread_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List all subagents spawned within a specific parent thread."""
    with _SUBAGENT_LOCK:
        items = [
            dict(v)
            for v in _SUBAGENT_REGISTRY.values()
            if v.get("parent_thread_id") == thread_id
        ]

    if status:
        items = [i for i in items if i.get("status") == status]

    items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return items[:limit]
