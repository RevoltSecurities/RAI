"""Pipeline endpoints: list pipelines, get pipeline details with batch topology."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from rai.harness.models import PipelineResponse, TaskResponse
from rai.harness.tasks import (
    build_pipeline_batches,
    get_pipeline_status,
    get_tasks_for_thread,
)
from rai.sessions.store import build_stream_config

router = APIRouter(tags=["pipelines"])


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


@router.get("/threads/{thread_id}/pipelines")
async def list_pipelines(thread_id: str, request: Request) -> list[dict[str, Any]]:
    """List all pipelines for a thread, grouped by pipeline_id."""
    tasks = await _get_thread_tasks(thread_id, request)
    pipeline_ids: list[str] = list({
        t["pipeline_id"] for t in tasks if t.get("pipeline_id")
    })

    result = []
    for pid in pipeline_ids:
        p_tasks = [t for t in tasks if t.get("pipeline_id") == pid]
        terminal_statuses = {"success", "error", "cancelled", "timeout", "skipped"}
        completed = sum(1 for t in p_tasks if t.get("status") == "success")
        failed = sum(1 for t in p_tasks if t.get("status") in ("error", "cancelled", "timeout"))
        result.append({
            "pipeline_id": pid,
            "status": get_pipeline_status(tasks, pid),
            "total_tasks": len(p_tasks),
            "completed_tasks": completed,
            "failed_tasks": failed,
        })
    return result


@router.get("/threads/{thread_id}/pipelines/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(
    thread_id: str, pipeline_id: str, request: Request
) -> PipelineResponse:
    tasks = await _get_thread_tasks(thread_id, request)
    p_tasks = [t for t in tasks if t.get("pipeline_id") == pipeline_id]
    if not p_tasks:
        raise HTTPException(status_code=404, detail=f"Pipeline '{pipeline_id}' not found")

    completed = sum(1 for t in p_tasks if t.get("status") == "success")
    failed = sum(1 for t in p_tasks if t.get("status") in ("error", "cancelled", "timeout"))
    batches = build_pipeline_batches(p_tasks)
    status = get_pipeline_status(tasks, pipeline_id)

    return PipelineResponse(
        pipeline_id=pipeline_id,
        status=status,
        total_tasks=len(p_tasks),
        completed_tasks=completed,
        failed_tasks=failed,
        batches=batches,
        tasks=[_task_dict_to_response(t) for t in p_tasks],
    )
