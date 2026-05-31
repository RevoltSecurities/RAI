"""Thread endpoints: list, get, state, history, delete, compact, summary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from langchain_core.messages import HumanMessage

from rai.harness.models import (
    CompactResponse,
    CompactStatusResponse,
    InjectMessageRequest,
    ThreadInfo,
    ThreadStateResponse,
    ThreadSummaryResponse,
)
from rai.harness.runner import _RUN_REGISTRY
from rai.sessions.store import (
    build_stream_config,
    delete_thread_sync,
    get_thread_by_id_sync,
    list_threads_sync,
    thread_exists_sync,
)

router = APIRouter(tags=["threads"])

_AVG_CHARS_PER_TOKEN = 4


def _msg_to_dict(msg: Any) -> dict[str, Any]:
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        content = " ".join(
            c.get("text", "") if isinstance(c, dict) else str(c) for c in content
        )
    result: dict[str, Any] = {
        "type": getattr(msg, "type", "unknown"),
        "content": str(content)[:8000],
        "id": getattr(msg, "id", None),
    }
    # AIMessage: expose tool_calls so TUI can render call/result chains
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.get("id", ""),
                "name": tc.get("name", ""),
                "args": tc.get("args", {}),
            }
            for tc in tool_calls
        ]
    # ToolMessage: expose correlation fields for pairing with call widgets
    tool_call_id = getattr(msg, "tool_call_id", None)
    if tool_call_id:
        result["tool_call_id"] = tool_call_id
    name = getattr(msg, "name", None)
    if name:
        result["name"] = name
    return result


@router.get("/threads", response_model=list[ThreadInfo])
async def list_threads(
    agent: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
    sort: str = Query("updated"),
) -> list[ThreadInfo]:
    rows = list_threads_sync(agent_name=agent, limit=limit, sort_by=sort)
    return [ThreadInfo(**r) for r in rows]


@router.get("/threads/{thread_id}", response_model=ThreadInfo)
async def get_thread(thread_id: str) -> ThreadInfo:
    thread = get_thread_by_id_sync(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
    return ThreadInfo(**thread)


@router.get("/threads/{thread_id}/state", response_model=ThreadStateResponse)
async def get_thread_state(thread_id: str, request: Request) -> ThreadStateResponse:
    pool = request.app.state.pool

    _ti = get_thread_by_id_sync(thread_id)
    agent_name = (_ti.get("agent_name") if _ti else None) or (pool.agent_names() or [None])[0]
    if not agent_name:
        raise HTTPException(status_code=503, detail="No agents registered")

    graph = await pool.get_graph(agent_name)
    config = build_stream_config(thread_id, agent_name, cwd=str(Path.cwd()))

    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    values = snapshot.values or {}
    messages = [_msg_to_dict(m) for m in values.get("messages", [])]
    local_tasks = {k: dict(v) for k, v in (values.get("local_async_tasks") or {}).items()}
    next_nodes = list(snapshot.next) if snapshot.next else []
    metadata = dict(snapshot.config.get("metadata", {})) if snapshot.config else {}

    return ThreadStateResponse(
        thread_id=thread_id,
        messages=messages,
        local_async_tasks=local_tasks,
        next_nodes=next_nodes,
        metadata=metadata,
    )


@router.get("/threads/{thread_id}/history")
async def get_thread_history(
    thread_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    pool = request.app.state.pool
    _ti = get_thread_by_id_sync(thread_id)
    agent_name = (_ti.get("agent_name") if _ti else None) or (pool.agent_names() or [None])[0]
    if not agent_name:
        raise HTTPException(status_code=503, detail="No agents registered")

    graph = await pool.get_graph(agent_name)
    config = build_stream_config(thread_id, agent_name, cwd=str(Path.cwd()))

    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    msgs = snapshot.values.get("messages", []) if snapshot.values else []
    total = len(msgs)
    page = msgs[offset: offset + limit]
    return {
        "thread_id": thread_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "messages": [_msg_to_dict(m) for m in page],
    }


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str) -> dict:
    deleted = delete_thread_sync(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
    return {"thread_id": thread_id, "deleted": True}


@router.get("/threads/{thread_id}/runs")
async def list_thread_runs(
    thread_id: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """D1 — List all runs that used this thread, newest first."""
    runs = [r for r in _RUN_REGISTRY.values() if r.get("thread_id") == thread_id]
    if status:
        runs = [r for r in runs if r.get("status") == status]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return runs[:limit]


# ── Summarization endpoints ────────────────────────────────────────────────────


@router.post("/threads/{thread_id}/compact", response_model=CompactResponse)
async def compact_thread(thread_id: str, request: Request) -> CompactResponse:
    """Trigger manual context compaction by injecting /compact into the thread."""
    pool = request.app.state.pool
    _ti = get_thread_by_id_sync(thread_id)
    agent_name = (_ti.get("agent_name") if _ti else None) or (pool.agent_names() or [None])[0]
    if not agent_name:
        raise HTTPException(status_code=503, detail="No agents registered")

    graph = await pool.get_graph(agent_name)
    config = build_stream_config(thread_id, agent_name, cwd=str(Path.cwd()))

    # Snapshot before so we can detect if compaction actually ran
    try:
        before_snapshot = await graph.aget_state(config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    before_msgs = (before_snapshot.values or {}).get("messages", [])
    before_count = len(before_msgs)
    before_event = (before_snapshot.values or {}).get("_summarization_event")

    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="/compact")]},
            config=config,
        )
        msgs = result.get("messages", []) if isinstance(result, dict) else []

        # Detect real compaction: tool path adds a "Conversation compacted" ToolMessage;
        # auto-trigger path updates _summarization_event but adds no ToolMessage.
        new_msgs = msgs[before_count:] if len(msgs) > before_count else []
        tool_confirmed = any(
            "Conversation compacted" in str(getattr(m, "content", ""))
            for m in new_msgs
        )
        after_event = result.get("_summarization_event") if isinstance(result, dict) else None
        event_changed = after_event is not None and after_event != before_event

        did_compact = tool_confirmed or event_changed
        status = "compacted" if did_compact else "no_change"

        return CompactResponse(
            status=status,
            thread_id=thread_id,
            message_count_after=len(msgs),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/threads/{thread_id}/compact/status", response_model=CompactStatusResponse)
async def compact_status(thread_id: str, request: Request) -> CompactStatusResponse:
    """Return token usage estimate and compaction recommendation."""
    pool = request.app.state.pool
    _ti = get_thread_by_id_sync(thread_id)
    agent_name = (_ti.get("agent_name") if _ti else None) or (pool.agent_names() or [None])[0]
    if not agent_name:
        raise HTTPException(status_code=503, detail="No agents registered")

    graph = await pool.get_graph(agent_name)
    config = build_stream_config(thread_id, agent_name, cwd=str(Path.cwd()))

    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    values = snapshot.values or {}
    raw_msgs = values.get("messages", [])

    # Apply _summarization_event if present — same reduction the model actually sees.
    # Raw message count is meaningless once compact has run (messages are never deleted).
    summ_event = values.get("_summarization_event")
    if summ_event and isinstance(summ_event, dict):
        cutoff = summ_event.get("cutoff_index", 0)
        summary_msg = summ_event.get("summary_message")
        effective: list[Any] = []
        if summary_msg is not None:
            effective.append(summary_msg)
        effective.extend(raw_msgs[cutoff:] if cutoff < len(raw_msgs) else raw_msgs[-20:])
    else:
        effective = raw_msgs

    total_chars = sum(len(str(getattr(m, "content", ""))) for m in effective)
    estimated_tokens = total_chars // _AVG_CHARS_PER_TOKEN
    # 200k context window; recommend compact at 75% fill
    should_compact = estimated_tokens > 150_000

    return CompactStatusResponse(
        thread_id=thread_id,
        message_count=len(effective),
        estimated_tokens=estimated_tokens,
        should_compact=should_compact,
    )


@router.get("/threads/{thread_id}/summary", response_model=ThreadSummaryResponse)
async def get_thread_summary(thread_id: str, request: Request) -> ThreadSummaryResponse:
    """Return the most recent summarization text from the thread checkpoint."""
    pool = request.app.state.pool
    _ti = get_thread_by_id_sync(thread_id)
    agent_name = (_ti.get("agent_name") if _ti else None) or (pool.agent_names() or [None])[0]
    if not agent_name:
        raise HTTPException(status_code=503, detail="No agents registered")

    graph = await pool.get_graph(agent_name)
    config = build_stream_config(thread_id, agent_name, cwd=str(Path.cwd()))

    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    msgs = snapshot.values.get("messages", []) if snapshot.values else []
    summary: str | None = None
    for m in reversed(msgs):
        msg_type = getattr(m, "type", "")
        content = str(getattr(m, "content", ""))
        if msg_type == "system" and "summary" in content.lower():
            summary = content
            break

    return ThreadSummaryResponse(thread_id=thread_id, summary=summary)


# ── D2 — Message injection ─────────────────────────────────────────────────────


@router.post("/threads/{thread_id}/messages", status_code=201)
async def inject_message(
    thread_id: str,
    body: InjectMessageRequest,
    request: Request,
) -> dict[str, Any]:
    """D2 — Write a HumanMessage directly into the thread checkpoint.

    Does NOT start execution — the message is visible in the thread state
    and will be processed on the next run. Matches the OpenAI Assistants API
    pattern where messages are added to a thread independently of runs.
    """
    pool = request.app.state.pool

    # Resolve which agent's graph to use for the checkpoint config
    agent_name = body.agent_name
    if agent_name is None:
        names = pool.agent_names()
        if not names:
            raise HTTPException(status_code=503, detail="No agents registered")
        agent_name = names[0]

    try:
        graph = await pool.get_graph(agent_name)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    config = build_stream_config(thread_id, agent_name, cwd=str(Path.cwd()))

    try:
        msg = HumanMessage(content=body.content)
        await graph.aupdate_state(config, {"messages": [msg]})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to inject message: {exc}") from exc

    return {
        "thread_id": thread_id,
        "agent_name": agent_name,
        "injected": True,
        "content_preview": body.content[:200],
    }
