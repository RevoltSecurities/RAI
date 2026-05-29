"""Typed SSE event dataclasses — one per bus.publish() call in the harness.

parse_event(raw) dispatches a raw SSEEvent to its typed dataclass.
All fields default to None/empty so partial server payloads never raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Raw SSE frame (lowest level)
# ---------------------------------------------------------------------------

@dataclass
class SSEEvent:
    event: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    raw: str = ""


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------

@dataclass
class RunStartEvent:
    run_id: str = ""
    thread_id: str = ""
    agent_name: str = ""
    input: str = ""
    model: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunEndEvent:
    run_id: str = ""
    thread_id: str = ""
    status: str = ""
    output: str = ""
    model: str = ""
    stop_reason: str | None = None
    result_subtype: str | None = None
    num_turns: int | None = None
    request_count: int | None = None
    duration_ms: int | None = None
    ttft_ms: float | None = None
    usage: dict[str, Any] | None = None
    model_usage: dict[str, Any] | None = None
    total_cost_usd: float | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunKeepaliveEvent:
    run_id: str = ""
    elapsed_ms: int = 0
    status: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RateLimitEvent:
    run_id: str = ""
    thread_id: str = ""
    status: str = ""
    resets_at: str | None = None
    rate_limit_type: str | None = None
    utilization: float | None = None
    overage_status: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorEvent:
    run_id: str = ""
    thread_id: str = ""
    message: str = ""
    traceback: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Token / thinking streaming
# ---------------------------------------------------------------------------

@dataclass
class TokenEvent:
    run_id: str = ""
    thread_id: str = ""
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThinkingEvent:
    run_id: str = ""
    thread_id: str = ""
    content: str = ""
    redacted: bool = False
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool events (agent)
# ---------------------------------------------------------------------------

@dataclass
class ToolStartEvent:
    run_id: str = ""
    thread_id: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolEndEvent:
    run_id: str = ""
    thread_id: str = ""
    tool_name: str = ""
    tool_output: Any = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PermissionDeniedEvent:
    run_id: str = ""
    thread_id: str = ""
    tool_name: str = ""
    reason: str = ""
    allowed_tools: list[str] | None = None
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# HITL events (agent)
# ---------------------------------------------------------------------------

@dataclass
class InterruptEvent:
    run_id: str = ""
    thread_id: str = ""
    interrupt_id: str = ""
    action_requests: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class InterruptResolvedEvent:
    run_id: str = ""
    thread_id: str = ""
    interrupt_id: str = ""
    decision: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class InterruptAutoApprovedEvent:
    run_id: str = ""
    thread_id: str = ""
    tool_names: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AskUserRequestEvent:
    run_id: str = ""
    thread_id: str = ""
    questions: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionApprovedEvent:
    run_id: str = ""
    thread_id: str = ""
    approved_tools: list[str] = field(default_factory=list)
    session_approved_tools: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Plan mode events
# ---------------------------------------------------------------------------

@dataclass
class PlanModeEnteredEvent:
    run_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanReadyEvent:
    run_id: str = ""
    plan: str = ""
    plan_file: str = ""
    approve_url: str = ""
    reject_url: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanApprovedEvent:
    run_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanRejectedEvent:
    run_id: str = ""
    feedback: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepCompleteEvent:
    run_id: str = ""
    step_number: int = 0
    description: str = ""
    notes: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepStartEvent:
    run_id: str = ""
    step_number: int = 0
    description: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepBlockedEvent:
    run_id: str = ""
    step_number: int = 0
    description: str = ""
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanCompletedEvent:
    run_id: str = ""
    plan_file: str = ""
    total_steps: int = 0
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Task / pipeline events
# ---------------------------------------------------------------------------

@dataclass
class TaskCreatedEvent:
    run_id: str = ""
    thread_id: str = ""
    task_id: str = ""
    agent_name: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskStatusEvent:
    run_id: str = ""
    thread_id: str = ""
    task_id: str = ""
    status: str = ""
    agent_name: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskCompletedEvent:
    run_id: str = ""
    thread_id: str = ""
    task_id: str = ""
    status: str = ""
    agent_name: str = ""
    output: str | None = None
    output_file: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotificationEvent:
    run_id: str = ""
    thread_id: str = ""
    task_id: str = ""
    agent_name: str = ""
    status: str = ""
    output_preview: str = ""
    output_file: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineCreatedEvent:
    run_id: str = ""
    thread_id: str = ""
    pipeline_id: str = ""
    tasks: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineBatchStartedEvent:
    run_id: str = ""
    pipeline_id: str = ""
    batch_num: int = 0
    task_ids: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineBatchEvent:
    run_id: str = ""
    thread_id: str = ""
    pipeline_id: str = ""
    batch_num: int = 0
    completed: int = 0
    failed: int = 0
    labels: list[str] = field(default_factory=list)
    batch_key: str = ""
    output_preview: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineEndEvent:
    run_id: str = ""
    thread_id: str = ""
    pipeline_id: str = ""
    status: str = ""
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    output: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineErrorEvent:
    run_id: str = ""
    pipeline_id: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class WatcherInvokeEvent:
    run_id: str = ""
    thread_id: str = ""
    reason: str = ""
    pending_count: int = 0
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Subagent events (emitted on both run SSE and subagent SSE via _emit_both)
# ---------------------------------------------------------------------------

@dataclass
class SubagentStartedEvent:
    run_id: str = ""
    task_id: str = ""
    agent_name: str = ""
    input: str = ""
    parent_run_id: str = ""
    model: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentTokenEvent:
    run_id: str = ""
    task_id: str = ""
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentThinkingEvent:
    run_id: str = ""
    task_id: str = ""
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentToolStartEvent:
    run_id: str = ""
    task_id: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentToolEndEvent:
    run_id: str = ""
    task_id: str = ""
    tool_name: str = ""
    tool_output: Any = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentInterruptEvent:
    run_id: str = ""
    task_id: str = ""
    agent_name: str = ""
    interrupt_id: str = ""
    action_requests: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentInterruptResolvedEvent:
    run_id: str = ""
    task_id: str = ""
    interrupt_id: str = ""
    decision: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentErrorEvent:
    run_id: str = ""
    task_id: str = ""
    agent_name: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentCompletedEvent:
    run_id: str = ""
    task_id: str = ""
    agent_name: str = ""
    status: str = ""
    output_preview: str = ""
    output_file: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentResumedEvent:
    run_id: str = ""
    task_id: str = ""
    agent_name: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentTurnCompleteEvent:
    run_id: str = ""
    task_id: str = ""
    agent_name: str = ""
    status: str = ""
    output_preview: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_EVENT_MAP: dict[str, type] = {
    "run_start": RunStartEvent,
    "run_end": RunEndEvent,
    "run_keepalive": RunKeepaliveEvent,
    "rate_limit": RateLimitEvent,
    "error": ErrorEvent,
    "token": TokenEvent,
    "thinking": ThinkingEvent,
    "tool_start": ToolStartEvent,
    "tool_end": ToolEndEvent,
    "permission_denied": PermissionDeniedEvent,
    "interrupt": InterruptEvent,
    "interrupt_resolved": InterruptResolvedEvent,
    "interrupt_auto_approved": InterruptAutoApprovedEvent,
    "ask_user_request": AskUserRequestEvent,
    "session_approved": SessionApprovedEvent,
    "plan_mode_entered": PlanModeEnteredEvent,
    "plan_ready": PlanReadyEvent,
    "plan_approved": PlanApprovedEvent,
    "plan_rejected": PlanRejectedEvent,
    "step_complete":   StepCompleteEvent,
    "step_start":      StepStartEvent,
    "step_blocked":    StepBlockedEvent,
    "plan_completed":  PlanCompletedEvent,
    "task_created": TaskCreatedEvent,
    "task_status": TaskStatusEvent,
    "task_completed": TaskCompletedEvent,
    "notification": NotificationEvent,
    "pipeline_created": PipelineCreatedEvent,
    "pipeline_batch_started": PipelineBatchStartedEvent,
    "pipeline_batch": PipelineBatchEvent,
    "pipeline_end": PipelineEndEvent,
    "pipeline_error": PipelineErrorEvent,
    "watcher_invoke": WatcherInvokeEvent,
    "subagent_started": SubagentStartedEvent,
    "subagent_token": SubagentTokenEvent,
    "subagent_thinking": SubagentThinkingEvent,
    "subagent_tool_start": SubagentToolStartEvent,
    "subagent_tool_end": SubagentToolEndEvent,
    "subagent_interrupt": SubagentInterruptEvent,
    "subagent_interrupt_resolved": SubagentInterruptResolvedEvent,
    "subagent_error": SubagentErrorEvent,
    "subagent_completed": SubagentCompletedEvent,
    "subagent_resumed": SubagentResumedEvent,
    "subagent_turn_complete": SubagentTurnCompleteEvent,
}


def parse_event(raw: SSEEvent) -> Any:
    """Return a typed event dataclass for raw, or raw itself if type is unknown."""
    cls = _EVENT_MAP.get(raw.event)
    if cls is None:
        return raw
    d = dict(raw.data)
    d["run_id"] = d.get("run_id", "")
    d["data"] = raw.data
    fields = cls.__dataclass_fields__
    filtered = {k: v for k, v in d.items() if k in fields}
    return cls(**filtered)
