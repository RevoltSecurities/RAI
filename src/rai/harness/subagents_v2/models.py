"""Data models for the async subagent system (v2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, NotRequired, TypedDict

from langchain.agents.middleware.types import AgentState
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


class AsyncTask(TypedDict):
    """A tracked async subagent task persisted in LangGraph graph state."""

    task_id: str
    agent_name: str
    thread_id: str
    run_id: str
    status: str
    created_at: str
    last_checked_at: str
    last_updated_at: str
    label: str | None
    pipeline_id: str | None
    depends_on: list[str] | None
    output_preview: str


def _tasks_reducer(
    existing: dict[str, AsyncTask] | None,
    update: dict[str, AsyncTask],
) -> dict[str, AsyncTask]:
    """Merge task updates into existing tasks dict (LangGraph reducer)."""
    merged = dict(existing or {})
    merged.update(update)
    return merged


class AsyncSubAgentState(AgentState):
    """State extension for async subagent task tracking."""

    async_tasks: Annotated[NotRequired[dict[str, AsyncTask]], _tasks_reducer]


# ---------------------------------------------------------------------------
# Internal notifications
# ---------------------------------------------------------------------------


class CompletionNotification(TypedDict):
    """Pushed to the per-run completion queue when a subagent finishes."""

    task_id: str
    agent_name: str
    status: str
    output_preview: str
    output_file: str
    label: str | None


# ---------------------------------------------------------------------------
# Terminal statuses
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timeout", "error", "success"})


# ---------------------------------------------------------------------------
# Pydantic models (for FastAPI routes)
# ---------------------------------------------------------------------------


class CreateSubagentRunRequest(BaseModel):
    """Request body for POST /internal/subagents/runs."""

    agent_name: str
    prompt: str
    parent_run_id: str
    parent_thread_id: str
    label: str | None = None
    model: str = ""
    system_prompt: str = ""
    pipeline_id: str | None = None
    depends_on: list[str] | None = None
    timeout: float = 3600.0


class SubagentRunResponse(BaseModel):
    """Response for POST /internal/subagents/runs."""

    task_id: str
    status: str
    agent_name: str
    label: str | None = None
    stream_url: str = ""
    created_at: str = ""


class SubagentStatusResponse(BaseModel):
    """Response for GET /internal/subagents/runs/{task_id}."""

    task_id: str
    status: str
    agent_name: str
    label: str | None = None
    output: str | None = None
    output_preview: str = ""
    created_at: str = ""
    last_updated_at: str = ""
    interrupt_pending: bool = False


class HITLDecisionRequest(BaseModel):
    """Request body for POST /internal/subagents/runs/{task_id}/interrupt."""

    decision_type: str = Field(description="One of: approve, reject, edit, respond")
    message: str = ""
    edits: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_task(
    task_id: str,
    agent_name: str,
    *,
    label: str | None = None,
    pipeline_id: str | None = None,
    depends_on: list[str] | None = None,
) -> AsyncTask:
    """Create a fresh AsyncTask in 'running' state."""
    now = _now()
    return AsyncTask(
        task_id=task_id,
        agent_name=agent_name,
        thread_id=task_id,
        run_id=task_id,
        status="running",
        created_at=now,
        last_checked_at=now,
        last_updated_at=now,
        label=label,
        pipeline_id=pipeline_id,
        depends_on=depends_on,
        output_preview="",
    )
