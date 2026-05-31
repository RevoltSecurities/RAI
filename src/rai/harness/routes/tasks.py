"""Task pool endpoints: per-thread and global task views."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from rai.agents.background import (
    _NOTIF_LOCK,
    _TASK_OUTPUT_QUEUES,
    _TASK_RUNNABLES,
    _TERMINAL_STATUSES,
)
from rai.harness.models import TaskResponse, TaskUpdateRequest
from rai.harness.tasks import (
    get_all_live_tasks,
    get_task_live_progress,
    get_tasks_for_thread,
)
from rai.sessions.store import build_stream_config

router = APIRouter(tags=["tasks"])


def _task_dict_to_response(t: dict[str, Any]) -> TaskResponse:
    return TaskResponse(
        task_id=t.get("task_id", ""),
        agent_name=t.get("agent_name", ""),
        status=t.get("status", "unknown"),
        created_at=t.get("created_at", ""),
        last_checked_at=t.get("last_checked_at", ""),
        output_file=t.get("output_file", ""),
        label=t.get("label"),
        depends_on=t.get("depends_on"),
        pipeline_id=t.get("pipeline_id"),
        output=t.get("output"),
    )


async def _get_thread_tasks(thread_id: str, request: Request) -> list[dict[str, Any]]:
    pool = request.app.state.pool
    agent_names = pool.agent_names()
    if not agent_names:
        return []
    graph = await pool.get_graph(agent_names[0])
    config = build_stream_config(thread_id, agent_names[0], cwd=str(Path.cwd()))
    try:
        snapshot = await graph.aget_state(config)
        return get_tasks_for_thread(snapshot.values or {})
    except Exception:
        return []


@router.get("/threads/{thread_id}/tasks", response_model=list[TaskResponse])
async def list_thread_tasks(
    thread_id: str,
    request: Request,
    status: str | None = Query(None),
) -> list[TaskResponse]:
    tasks = await _get_thread_tasks(thread_id, request)
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    return [_task_dict_to_response(t) for t in tasks]


@router.get("/threads/{thread_id}/tasks/{task_id}", response_model=TaskResponse)
async def get_task(thread_id: str, task_id: str, request: Request) -> TaskResponse:
    tasks = await _get_thread_tasks(thread_id, request)
    for t in tasks:
        if t.get("task_id") == task_id:
            return _task_dict_to_response(t)
    raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")


@router.get("/threads/{thread_id}/tasks/{task_id}/progress")
async def get_task_progress(thread_id: str, task_id: str) -> dict:
    progress = await get_task_live_progress(task_id)
    if progress is None:
        raise HTTPException(status_code=404, detail=f"No live progress for task '{task_id}'")
    return {"task_id": task_id, "progress": progress}


@router.post("/threads/{thread_id}/tasks/{task_id}/cancel")
async def cancel_task(thread_id: str, task_id: str) -> dict:
    from rai.agents.background import _TASK_REGISTRY, _MANUALLY_CANCELLED
    with _NOTIF_LOCK:
        task = _TASK_REGISTRY.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not running")
    if not task.done():
        _MANUALLY_CANCELLED.add(task_id)
        task.cancel()
    return {"task_id": task_id, "cancelled": True}


@router.post("/threads/{thread_id}/tasks/{task_id}/update")
async def update_task(thread_id: str, task_id: str, body: TaskUpdateRequest) -> dict:
    """Send a follow-up message to a completed task (relaunches from checkpoint)."""
    import uuid
    from pathlib import Path as _Path
    from rai.agents.background import _launch_task

    entry = _TASK_RUNNABLES.get(task_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No runnable found for task '{task_id}'")

    runnable, timeout = entry
    new_task_id = uuid.uuid4().hex
    out_path = _Path(f"/tmp/rai_task_{new_task_id[:12]}.json")

    _launch_task(
        runnable=runnable,
        state={},
        task_id=task_id,  # reuse same thread checkpoint
        out_path=out_path,
        initial_message=body.message,
        agent_name=f"followup-{task_id[:8]}",
        timeout=timeout,
    )
    return {"original_task_id": task_id, "new_task_id": task_id, "message": body.message}


@router.get("/threads/{thread_id}/tasks/{task_id}/response")
async def get_task_response(
    thread_id: str,
    task_id: str,
    timeout: float = Query(120.0, ge=1.0, le=3600.0),
) -> dict:
    """Block until the task puts a response in _TASK_OUTPUT_QUEUES."""
    queue = _TASK_OUTPUT_QUEUES.get(task_id)
    if queue is None:
        raise HTTPException(status_code=404, detail=f"No output queue for task '{task_id}'")
    try:
        response = await asyncio.wait_for(queue.get(), timeout=timeout)
        return {"task_id": task_id, "response": response}
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408,
            detail=f"Timed out waiting for task '{task_id}' response after {timeout}s",
        )


# Global task views

@router.get("/tasks", response_model=list[TaskResponse])
async def list_all_tasks() -> list[TaskResponse]:
    live = get_all_live_tasks()
    return [
        TaskResponse(
            task_id=tid,
            agent_name=info.get("agent_name", ""),
            status=info.get("status", "running"),
            created_at="",
            last_checked_at="",
            output_file="",
        )
        for tid, info in live.items()
    ]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_global_task(task_id: str) -> TaskResponse:
    live = get_all_live_tasks()
    info = live.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return TaskResponse(
        task_id=task_id,
        agent_name=info.get("agent_name", ""),
        status=info.get("status", "running"),
        created_at="",
        last_checked_at="",
        output_file="",
    )
