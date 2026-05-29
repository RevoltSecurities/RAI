"""Run endpoints: create, stream, status, cancel, list."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from rai.harness.models import CreateRunRequest, PlanDecisionRequest, RunDetailResponse, RunResponse
from rai.harness.runner import (
    _PLAN_FUTURES,
    _RUN_REGISTRY,
    _RUN_TASKS,
    execute_run,
    generate_run_id,
    list_runs_for_thread,
)
from rai.harness.sse import RunEventBus, ThreadNotifBus
from rai.sessions.store import generate_thread_id

router = APIRouter(tags=["runs"])


@router.post("/agents/{name}/runs", response_model=RunResponse)
async def create_run(name: str, body: CreateRunRequest, request: Request) -> RunResponse:
    pool = request.app.state.pool
    config = request.app.state.config
    checkpointer = request.app.state.checkpointer

    # Ensure graph is compiled (lazy first-request compilation)
    await pool.get_graph(name)

    run_id = generate_run_id()
    thread_id = body.thread_id or generate_thread_id()
    cwd = str(Path.cwd())
    created_at = datetime.now(UTC).isoformat()

    pool_model_hint = pool.get_model_hint(name)

    # B2 — per-run model override: compile a fresh variant graph with the requested model
    if body.model and (":" not in body.model and "/" not in body.model):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid model string {body.model!r}. "
                "Use 'provider:model' (e.g. 'anthropic:claude-sonnet-4-6') "
                "or 'litellm:provider/model' (e.g. 'litellm:openai/bedrock-claude-sonnet-4.6-(US)')."
            ),
        )
    if body.model and body.model != pool_model_hint:
        try:
            runnable = await pool.build_model_variant(name, thread_id, body.model, cwd, checkpointer)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Model variant compilation failed: {exc}") from exc
        model_hint = body.model
    else:
        # Per-run lightweight SDK wrapper — shares the compiled graph but has its
        # own thread_id/config so concurrent runs on different threads are isolated.
        runnable = pool.create_per_run_runnable(name, thread_id, name, cwd)
        model_hint = pool_model_hint

    bus = RunEventBus.create(run_id)
    notif_bus = ThreadNotifBus.create(thread_id)

    # B3 — per-run allowed_tools (overrides agent-level default from E1).
    # Copy the list so the HITL approve_for_session branch cannot mutate the pool's stored reference.
    _pool_tools = pool.get_allowed_tools(name)
    effective_allowed_tools: list[str] | None = (
        list(body.allowed_tools) if body.allowed_tools is not None
        else (list(_pool_tools) if _pool_tools is not None else None)
    )

    _RUN_TASKS[run_id] = asyncio.create_task(
        execute_run(
            run_id=run_id,
            thread_id=thread_id,
            agent_name=name,
            runnable=runnable,
            input_message=body.input,
            bus=bus,
            notif_bus=notif_bus,
            timeout=config.max_run_timeout,
            metadata=body.metadata,
            model_hint=model_hint,
            allowed_tools=effective_allowed_tools,
            max_turns=body.max_turns,
            checkpointer=checkpointer,
            recursion_limit=body.recursion_limit or 100,
            plan_mode=body.plan_mode,
            self_learn=body.self_learn,
        ),
        name=f"rai-run-{run_id[:8]}",
    )

    stream_url = f"/agents/{name}/runs/{run_id}/stream"
    return RunResponse(
        run_id=run_id,
        thread_id=thread_id,
        agent_name=name,
        status="running",
        stream_url=stream_url,
        created_at=created_at,
    )


# ── D1: Run list endpoints ────────────────────────────────────────────────────

@router.get("/agents/{name}/runs")
async def list_agent_runs(
    name: str,
    status: str | None = Query(default=None, description="Filter by status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List all runs for a named agent, optionally filtered by status."""
    runs = [r for r in _RUN_REGISTRY.values() if r.get("agent_name") == name]
    if status:
        runs = [r for r in runs if r.get("status") == status]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return runs[offset: offset + limit]


@router.get("/agents/{name}/runs/{run_id}")
async def get_run_status(name: str, run_id: str) -> dict[str, Any]:
    run = _RUN_REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


@router.get("/runs/{run_id}")
async def get_run_by_id(run_id: str) -> dict[str, Any]:
    """Fetch a run by ID without knowing the agent name."""
    run = _RUN_REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


@router.get("/runs")
async def list_all_runs(
    status: str | None = Query(default=None),
    agent: str | None = Query(default=None, description="Filter by agent name"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """Global run list across all agents."""
    runs = list(_RUN_REGISTRY.values())
    if status:
        runs = [r for r in runs if r.get("status") == status]
    if agent:
        runs = [r for r in runs if r.get("agent_name") == agent]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return runs[offset: offset + limit]


@router.get("/agents/{name}/runs/{run_id}/stream")
async def stream_run(name: str, run_id: str, request: Request) -> StreamingResponse:
    """SSE stream — attach to a running or just-created run.

    D3: Pass 'Last-Event-ID' header to resume replay from a specific event ID
    without losing events that occurred during a network blip.
    """
    bus = RunEventBus.create(run_id)

    # D3 — parse Last-Event-ID header for replay
    raw_last_id = request.headers.get("Last-Event-ID") or request.headers.get("last-event-id")
    last_event_id: int | None = None
    if raw_last_id is not None:
        try:
            last_event_id = int(raw_last_id)
        except (ValueError, TypeError):
            pass

    async def _event_gen():
        try:
            async for frame in bus.subscribe(last_event_id=last_event_id):
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            pass

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@router.post("/agents/{name}/runs/{run_id}/cancel")
async def cancel_run(name: str, run_id: str) -> dict:
    run = _RUN_REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    if run.get("status") not in ("running", "interrupted"):
        return {"run_id": run_id, "status": run.get("status"), "cancelled": False}

    # Cancel the execute_run task for this specific run
    run_task = _RUN_TASKS.pop(run_id, None)
    if run_task and not run_task.done():
        run_task.cancel()

    # Cancel subagents spawned by this specific run
    from rai.harness.subagents.registry import _SUBAGENT_REGISTRY, _SUBAGENT_TASKS, _SUBAGENT_LOCK
    with _SUBAGENT_LOCK:
        subagent_tasks = [
            _SUBAGENT_TASKS.get(tid)
            for tid, meta in _SUBAGENT_REGISTRY.items()
            if meta.get("parent_run_id") == run_id
        ]
    for stask in subagent_tasks:
        if stask and not stask.done():
            stask.cancel()

    run["status"] = "cancelled"
    RunEventBus.cleanup(run_id)
    return {"run_id": run_id, "status": "cancelled", "cancelled": True}


# ── Plan mode endpoints ───────────────────────────────────────────────────────

@router.get("/agents/{name}/runs/{run_id}/plan")
async def get_run_plan(name: str, run_id: str) -> dict:
    """Get the current plan submitted by the agent (if any)."""
    run = _RUN_REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    plan = run.get("plan")
    if plan is None:
        raise HTTPException(status_code=404, detail="No plan submitted yet for this run")
    return {
        "run_id": run_id,
        "status": run.get("status"),
        "plan": plan,
        "plan_file": run.get("plan_file", ""),
        "plan_steps": run.get("plan_steps", []),
    }


@router.post("/agents/{name}/runs/{run_id}/plan/approve")
async def approve_run_plan(name: str, run_id: str) -> dict:
    """Approve the agent's plan — unblocks execution."""
    run = _RUN_REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    fut = _PLAN_FUTURES.get(run_id)
    if fut is None or fut.done():
        raise HTTPException(status_code=409, detail="No pending plan to approve")
    fut.set_result({"action": "approve"})
    return {"run_id": run_id, "decision": "approve", "resolved": True}


@router.post("/agents/{name}/runs/{run_id}/plan/reject")
async def reject_run_plan(name: str, run_id: str, body: PlanDecisionRequest) -> dict:
    """Reject the agent's plan with feedback — agent revises and resubmits."""
    run = _RUN_REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    fut = _PLAN_FUTURES.get(run_id)
    if fut is None or fut.done():
        raise HTTPException(status_code=409, detail="No pending plan to reject")
    fut.set_result({"action": "reject", "feedback": body.feedback or ""})
    return {"run_id": run_id, "decision": "reject", "resolved": True}


@router.post("/agents/{name}/runs/{run_id}/plan/edit")
async def edit_run_plan(name: str, run_id: str, body: PlanDecisionRequest) -> dict:
    """User-edited plan — auto-approves with the user's revised content."""
    run = _RUN_REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    fut = _PLAN_FUTURES.get(run_id)
    if fut is None or fut.done():
        raise HTTPException(status_code=409, detail="No pending plan to edit")
    fut.set_result({"action": "edit", "feedback": body.feedback or ""})
    return {"run_id": run_id, "decision": "edit", "resolved": True}


@router.post("/agents/{name}/runs/{run_id}/plan/respond")
async def respond_run_plan(name: str, run_id: str, body: PlanDecisionRequest) -> dict:
    """Reject with constructive guidance — agent incorporates feedback and rewrites."""
    run = _RUN_REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    fut = _PLAN_FUTURES.get(run_id)
    if fut is None or fut.done():
        raise HTTPException(status_code=409, detail="No pending plan to respond to")
    fut.set_result({"action": "respond", "feedback": body.feedback or ""})
    return {"run_id": run_id, "decision": "respond", "resolved": True}
