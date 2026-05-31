"""Runtime agent endpoints: one-shot runs, dynamic agent registration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from rai.harness.models import (
    RegisterAgentRequest,
    RuntimeRunRequest,
    RunResponse,
)
from rai.harness.runner import _RUN_TASKS, execute_run, generate_run_id
from rai.harness.sse import RunEventBus, ThreadNotifBus
from rai.sessions.store import generate_thread_id

router = APIRouter(tags=["runtime"])


def _build_runtime_builder(
    name: str,
    model: str | None,
    system_prompt: str | None,
    system_prompt_extra: str | None,
    disable_native_tools: bool,
    disable_subagents: bool,
    api_key: str,
    base_url: str,
    description: str = "",
):
    from rai import DEFAULT_MODEL
    from rai.sdk import RAIAgent

    builder = RAIAgent.builder().agent_name(name).without_hitl()

    resolved_model = model or DEFAULT_MODEL
    builder = builder.model(resolved_model)

    if system_prompt:
        builder = builder.system_prompt(system_prompt)
    if system_prompt_extra:
        builder = builder.system_prompt_extra(system_prompt_extra)
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)
    if disable_native_tools:
        builder = builder.without_native_tools()
    if disable_subagents:
        builder = builder.without_subagents()

    return builder


@router.post("/agents/runtime/runs", response_model=RunResponse)
async def create_runtime_run(body: RuntimeRunRequest, request: Request) -> RunResponse:
    """Create a one-shot run with an inline agent definition (compiled fresh each time)."""
    from rai.engine.factory import create_rai_agent
    from rai import DEFAULT_MODEL

    checkpointer = request.app.state.checkpointer
    cfg = request.app.state.config
    agent_def = body.agent

    try:
        from rai.harness.subagents.tools import get_http_subagent_tools
        graph, _ = create_rai_agent(
            model=agent_def.model or DEFAULT_MODEL,
            agent_name=agent_def.name,
            system_prompt=agent_def.system_prompt or None,
            system_prompt_extra=agent_def.system_prompt_extra or None,
            disable_native_tools=agent_def.disable_native_tools,
            disable_subagents=agent_def.disable_subagents,
            extra_tools=get_http_subagent_tools(),
            checkpointer=checkpointer,
            interactive=False,
            auto_approve=True,
            api_key=agent_def.api_key or None,
            base_url=agent_def.base_url or None,
            suppress_local_async=True,
            disable_opplan=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to compile runtime agent: {exc}"
        ) from exc

    run_id = generate_run_id()
    thread_id = body.thread_id or generate_thread_id()
    cwd = str(Path.cwd())
    created_at = datetime.now(UTC).isoformat()

    # Wrap the freshly compiled graph in a per-run RunableAgent
    from rai.sdk import RunableAgent
    from rai.sessions.store import build_stream_config
    runnable = RunableAgent(
        graph=graph,
        backend=None,
        thread_id=thread_id,
        config=build_stream_config(thread_id, agent_def.name, cwd=cwd),
        agent_name=agent_def.name,
        cwd=cwd,
    )

    bus = RunEventBus.create(run_id)
    notif_bus = ThreadNotifBus.create(thread_id)

    _RUN_TASKS[run_id] = asyncio.create_task(
        execute_run(
            run_id=run_id,
            thread_id=thread_id,
            agent_name=agent_def.name,
            runnable=runnable,
            input_message=body.input,
            bus=bus,
            notif_bus=notif_bus,
            timeout=cfg.max_run_timeout,
            model_hint=agent_def.model or "",
            allowed_tools=list(agent_def.allowed_tools) if agent_def.allowed_tools is not None else None,
            checkpointer=checkpointer,
        ),
        name=f"rai-runtime-{run_id[:8]}",
    )

    return RunResponse(
        run_id=run_id,
        thread_id=thread_id,
        agent_name=agent_def.name,
        status="running",
        stream_url=f"/agents/runtime/runs/{run_id}/stream",
        created_at=created_at,
    )


@router.get("/agents/runtime/runs/{run_id}/stream")
async def stream_runtime_run(run_id: str, request: Request):
    from fastapi.responses import StreamingResponse
    from rai.harness.sse import RunEventBus as _Bus

    bus = _Bus.create(run_id)

    # D3 — parse Last-Event-ID header for replay
    raw_last_id = request.headers.get("Last-Event-ID") or request.headers.get("last-event-id")
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


@router.post("/agents", status_code=201)
async def register_agent(body: RegisterAgentRequest, request: Request) -> dict:
    """Register a named runtime agent in the pool (persists for the server lifetime)."""
    pool = request.app.state.pool
    checkpointer = request.app.state.checkpointer

    builder = _build_runtime_builder(
        name=body.name,
        model=body.model,
        system_prompt=body.system_prompt,
        system_prompt_extra=body.system_prompt_extra,
        disable_native_tools=body.disable_native_tools,
        disable_subagents=body.disable_subagents,
        api_key=body.api_key,
        base_url=body.base_url,
        description=body.description,
    )
    if checkpointer:
        builder = builder.checkpointer(checkpointer)

    pool.register_runtime(body.name, builder, allowed_tools=body.allowed_tools)
    return {"name": body.name, "registered": True}


@router.delete("/agents/{name}")
async def remove_agent(name: str, request: Request) -> dict:
    """Remove a runtime-registered agent from the pool."""
    pool = request.app.state.pool
    try:
        pool.remove(name)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"name": name, "removed": True}
