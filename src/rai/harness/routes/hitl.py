"""HITL interrupt endpoints: check, submit decision, stream interrupts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from rai.harness.models import AskUserAnswersRequest, InterruptDecisionRequest, InterruptResponse
from rai.harness.runner import _ASK_USER_FUTURES, _HITL_FUTURES, _RUN_REGISTRY, _SESSION_APPROVED
from rai.harness.sse import ThreadNotifBus, sse_frame
from rai.sessions.store import build_stream_config

router = APIRouter(tags=["hitl"])


def _stable_interrupt_id(intr: Any) -> str:
    """Return a deterministic 16-char hex ID for an interrupt object."""
    payload = json.dumps(intr.value, sort_keys=True, default=str) if isinstance(intr.value, dict) else str(intr.value)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def _get_interrupt_info(thread_id: str, request: Request) -> tuple[Any, Any]:
    """Return (graph, snapshot) for a thread, or raise 503.

    Looks up the agent_name associated with this thread from the run registry
    so the correct compiled graph is used in multi-agent deployments.
    """
    pool = request.app.state.pool
    # Find the agent associated with this thread from recent runs
    agent_name: str | None = None
    for run in _RUN_REGISTRY.values():
        if run.get("thread_id") == thread_id:
            agent_name = run.get("agent_name")
            break
    if agent_name is None:
        agent_names = pool.agent_names()
        if not agent_names:
            raise HTTPException(status_code=503, detail="No agents registered")
        agent_name = agent_names[0]
    graph = await pool.get_graph(agent_name)
    config = build_stream_config(thread_id, agent_name, cwd=str(Path.cwd()))
    snapshot = await graph.aget_state(config)
    return graph, snapshot


@router.get("/threads/{thread_id}/interrupt", response_model=InterruptResponse)
async def get_interrupt(thread_id: str, request: Request) -> InterruptResponse:
    """Check whether a HITL interrupt is pending for this thread."""
    try:
        _, snapshot = await _get_interrupt_info(thread_id, request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not (snapshot.tasks and snapshot.tasks[0].interrupts):
        return InterruptResponse(pending=False, thread_id=thread_id)

    intr = snapshot.tasks[0].interrupts[0]
    interrupt_id = _stable_interrupt_id(intr)
    action_requests: list[dict] = []
    if isinstance(intr.value, dict):
        action_requests = intr.value.get("action_requests", [])

    _session_tools = list(_SESSION_APPROVED.get(thread_id, set()))

    return InterruptResponse(
        pending=True,
        interrupt_id=interrupt_id,
        action_requests=action_requests,
        thread_id=thread_id,
        session_approved_tools=_session_tools,
    )


@router.post("/threads/{thread_id}/interrupt")
async def submit_interrupt_decision(
    thread_id: str, body: InterruptDecisionRequest
) -> dict:
    """Submit a HITL decision, unblocking the waiting execute_run() coroutine."""
    # Find the interrupted run_id for this thread — futures are keyed by run_id
    run_id: str | None = None
    for rid, rmeta in _RUN_REGISTRY.items():
        if rmeta.get("thread_id") == thread_id and rmeta.get("status") == "interrupted":
            run_id = rid
            break
    if run_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"No pending HITL interrupt for thread '{thread_id}'",
        )
    fut = _HITL_FUTURES.get(run_id)
    if fut is None:
        raise HTTPException(
            status_code=409,
            detail=f"No pending HITL interrupt for thread '{thread_id}'",
        )
    if fut.done():
        raise HTTPException(status_code=409, detail="Interrupt already resolved")

    # Translate to runner decision format
    if body.decision == "approve":
        decision: dict[str, Any] = {"type": "approve"}

    elif body.decision == "reject":
        decision = {"type": "reject"}
        if body.message:
            decision["message"] = body.message

    elif body.decision == "edit":
        if body.edited_action is None:
            raise HTTPException(
                status_code=422,
                detail="'edited_action' is required when decision='edit'",
            )
        decision = {"type": "edit", "edited_action": body.edited_action}

    elif body.decision == "respond":
        if not body.message:
            raise HTTPException(
                status_code=422,
                detail="'message' is required when decision='respond'",
            )
        decision = {"type": "respond", "message": body.message}

    elif body.decision == "approve_for_session":
        # Runner handles session tracking; signal via type field
        decision = {"type": "approve_for_session"}

    else:
        raise HTTPException(status_code=422, detail=f"Unknown decision: {body.decision!r}")

    fut.set_result(decision)
    return {"thread_id": thread_id, "run_id": run_id, "decision": body.decision, "resolved": True}


@router.get("/threads/{thread_id}/interrupt/stream")
async def stream_interrupts(thread_id: str, request: Request) -> StreamingResponse:
    """SSE stream that emits only interrupt events for this thread."""
    notif_bus = ThreadNotifBus.create(thread_id)

    async def _gen():
        # Immediately check if there's already a pending interrupt
        try:
            _, snapshot = await _get_interrupt_info(thread_id, request)
            if snapshot.tasks and snapshot.tasks[0].interrupts:
                intr = snapshot.tasks[0].interrupts[0]
                interrupt_id = _stable_interrupt_id(intr)
                action_requests: list[dict] = []
                if isinstance(intr.value, dict):
                    action_requests = intr.value.get("action_requests", [])
                yield sse_frame("interrupt", {
                    "thread_id": thread_id,
                    "interrupt_id": interrupt_id,
                    "action_requests": action_requests,
                })
        except Exception:
            pass

        try:
            async for frame in notif_bus.subscribe():
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            notif_bus.detach()

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.post("/threads/{thread_id}/ask_user")
async def submit_ask_user_answers(thread_id: str, body: AskUserAnswersRequest) -> dict:
    """Submit answers to an ask_user interrupt, resuming the paused run."""
    run_id: str | None = None
    for rid, rmeta in _RUN_REGISTRY.items():
        if rmeta.get("thread_id") == thread_id and rmeta.get("status") == "interrupted":
            run_id = rid
            break
    if run_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"No pending ask_user for thread '{thread_id}'",
        )
    fut = _ASK_USER_FUTURES.get(run_id)
    if fut is None or fut.done():
        raise HTTPException(status_code=409, detail="No pending ask_user future")
    fut.set_result({"status": body.status, "answers": body.answers})
    return {"thread_id": thread_id, "run_id": run_id, "resolved": True}
