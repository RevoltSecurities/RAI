"""Pydantic models mirroring http_server/models.py — all with extra='allow'."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class UsageInfo(_Base):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class RunResponse(_Base):
    run_id: str = ""
    thread_id: str = ""
    agent_name: str = ""
    status: str = ""
    stream_url: str = ""
    created_at: str = ""


class RunDetailResponse(_Base):
    run_id: str = ""
    thread_id: str = ""
    agent_name: str = ""
    status: str = ""
    input: str = ""
    output: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = {}
    model: str = ""
    stop_reason: str | None = None
    result_subtype: str | None = None
    num_turns: int | None = None
    request_count: int | None = None
    duration_ms: int | None = None
    ttft_ms: float | None = None
    usage: UsageInfo | None = None
    model_usage: dict[str, Any] | None = None
    total_cost_usd: float | None = None


class CreateRunRequest(_Base):
    input: str
    thread_id: str | None = None
    config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    model: str | None = None
    allowed_tools: list[str] | None = None
    max_turns: int | None = None
    recursion_limit: int | None = None
    plan_mode: bool = False
    self_learn: bool = False


class RuntimeAgentDef(_Base):
    name: str = "runtime"
    description: str = ""
    system_prompt: str | None = None
    system_prompt_extra: str | None = None
    model: str | None = None
    disable_native_tools: bool = False
    disable_subagents: bool = True
    api_key: str = ""
    base_url: str = ""
    allowed_tools: list[str] | None = None


class RuntimeRunRequest(_Base):
    input: str
    agent: RuntimeAgentDef
    thread_id: str | None = None
    config: dict[str, Any] | None = None


class RegisterAgentRequest(_Base):
    name: str
    description: str = ""
    system_prompt: str | None = None
    system_prompt_extra: str | None = None
    model: str | None = None
    disable_native_tools: bool = False
    disable_subagents: bool = True
    api_key: str = ""
    base_url: str = ""
    allowed_tools: list[str] | None = None


class InterruptResponse(_Base):
    pending: bool = False
    interrupt_id: str | None = None
    action_requests: list[dict[str, Any]] | None = None
    thread_id: str = ""
    session_approved_tools: list[str] = []


class InterruptDecisionRequest(_Base):
    decision: str
    edited_action: dict[str, Any] | None = None
    message: str | None = None


class PlanDecisionRequest(_Base):
    feedback: str | None = None


class ThreadInfo(_Base):
    thread_id: str = ""
    agent_name: str | None = None
    updated_at: str | None = None
    created_at: str | None = None
    git_branch: str | None = None
    cwd: str | None = None


class ThreadStateResponse(_Base):
    thread_id: str = ""
    messages: list[dict[str, Any]] = []
    local_async_tasks: dict[str, dict[str, Any]] = {}
    next_nodes: list[str] = []
    metadata: dict[str, Any] = {}


class TaskResponse(_Base):
    task_id: str = ""
    agent_name: str = ""
    status: str = ""
    created_at: str = ""
    last_checked_at: str = ""
    output_file: str = ""
    label: str | None = None
    depends_on: list[str] | None = None
    pipeline_id: str | None = None
    output: str | None = None


class TaskUpdateRequest(_Base):
    message: str


class PipelineResponse(_Base):
    pipeline_id: str = ""
    status: str = ""
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    batches: list[dict[str, Any]] = []
    tasks: list[TaskResponse] = []


class AgentInfo(_Base):
    name: str = ""
    model: str = ""
    description: str = ""
    hitl_enabled: bool = False
    hitl_note: str = ""


class SubagentInfo(_Base):
    name: str = ""
    description: str = ""
    model: str = ""
    has_own_config: bool = False


class StatsResponse(_Base):
    active_runs: int = 0
    live_tasks: int = 0
    registered_agents: list[str] = []
    total_threads: int = 0


class CompactStatusResponse(_Base):
    thread_id: str = ""
    message_count: int = 0
    estimated_tokens: int = 0
    should_compact: bool = False


class CompactResponse(_Base):
    status: str = ""
    thread_id: str = ""
    message_count_after: int = 0


class ThreadSummaryResponse(_Base):
    thread_id: str = ""
    summary: str | None = None


class InjectMessageRequest(_Base):
    content: str
    agent_name: str | None = None


class AskUserAnswersRequest(_Base):
    status: str = "answered"   # "answered" | "cancelled"
    answers: list[str] = []
