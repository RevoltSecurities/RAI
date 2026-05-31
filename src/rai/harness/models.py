"""Pydantic request/response models for the RAI HTTP server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    input: str
    thread_id: str | None = None
    config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None   # B1
    model: str | None = None                  # B2 — override agent default model for this run
    allowed_tools: list[str] | None = None    # B3 — whitelist tool names; None = all tools allowed
    max_turns: int | None = None              # B6 — cap LLM turns; None = unlimited
    recursion_limit: int | None = Field(default=None, ge=1, le=500)  # override LangGraph recursion limit
    plan_mode: bool = False                   # When True, agent must plan + get approval before acting
    self_learn: bool = False                  # When True, post-run rule-based lessons are appended to lessons.md


class RunResponse(BaseModel):
    run_id: str
    thread_id: str
    agent_name: str
    status: str  # queued | running | completed | failed | interrupted
    stream_url: str
    created_at: str


class UsageInfo(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class RunDetailResponse(BaseModel):
    """Full run record returned by GET /agents/{name}/runs/{run_id}."""
    run_id: str
    thread_id: str
    agent_name: str
    status: str
    input: str
    output: str | None = None
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Model info — detected from first LLM event (ls_model_name / response_metadata)
    model: str = ""
    # A1-A7 enrichment fields (populated after run completes)
    stop_reason: str | None = None
    result_subtype: str | None = None
    num_turns: int | None = None
    request_count: int | None = None   # LLM API calls (≥ num_turns when tools used)
    duration_ms: int | None = None
    ttft_ms: float | None = None
    usage: UsageInfo | None = None
    model_usage: dict[str, Any] | None = None  # per-model token breakdown
    total_cost_usd: float | None = None         # always null (cost calc not implemented)


class TaskResponse(BaseModel):
    task_id: str
    agent_name: str
    status: str
    created_at: str
    last_checked_at: str
    output_file: str
    label: str | None = None
    depends_on: list[str] | None = None
    pipeline_id: str | None = None
    output: str | None = None


class PipelineResponse(BaseModel):
    pipeline_id: str
    status: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    batches: list[dict[str, Any]]
    tasks: list[TaskResponse]


class InterruptResponse(BaseModel):
    pending: bool
    interrupt_id: str | None = None
    action_requests: list[dict[str, Any]] | None = None
    thread_id: str
    session_approved_tools: list[str] = Field(default_factory=list)


class InterruptDecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "edit", "approve_for_session", "respond"]
    edited_action: dict[str, Any] | None = None   # for "edit": SDK format {name, args}
    message: str | None = None                     # for "reject" (reason) or "respond" (answer)


class PlanDecisionRequest(BaseModel):
    feedback: str | None = None   # Meaningful on reject; ignored on approve


class ThreadInfo(BaseModel):
    thread_id: str
    agent_name: str | None = None
    updated_at: str | None = None
    created_at: str | None = None
    git_branch: str | None = None
    cwd: str | None = None


class ThreadStateResponse(BaseModel):
    thread_id: str
    messages: list[dict[str, Any]]
    local_async_tasks: dict[str, dict[str, Any]]
    next_nodes: list[str]
    metadata: dict[str, Any]


class StatsResponse(BaseModel):
    active_runs: int
    live_tasks: int
    registered_agents: list[str]
    total_threads: int


class AgentInfo(BaseModel):
    name: str
    model: str
    description: str = ""
    hitl_enabled: bool = False
    hitl_note: str = ""     # non-empty when hitl_enabled=false, explains how to enable


class SubagentInfo(BaseModel):
    name: str
    description: str = ""
    model: str = ""           # empty = inherits from parent
    has_own_config: bool = False  # True when ~/.rai/agents/{name}/config.toml exists


# Runtime agent models

class RuntimeAgentDef(BaseModel):
    name: str = "runtime"
    description: str = ""
    system_prompt: str | None = None
    system_prompt_extra: str | None = None
    model: str | None = None
    disable_native_tools: bool = False
    disable_subagents: bool = True
    api_key: str = ""
    base_url: str = ""
    allowed_tools: list[str] | None = None   # whitelist for this one-shot run


class RuntimeRunRequest(BaseModel):
    input: str
    agent: RuntimeAgentDef
    thread_id: str | None = None
    config: dict[str, Any] | None = None


class RegisterAgentRequest(BaseModel):
    name: str
    description: str = ""
    system_prompt: str | None = None
    system_prompt_extra: str | None = None
    model: str | None = None
    disable_native_tools: bool = False
    disable_subagents: bool = True
    api_key: str = ""
    base_url: str = ""
    allowed_tools: list[str] | None = None   # E1 — applies to all runs on this agent


# Summarization models

class CompactStatusResponse(BaseModel):
    thread_id: str
    message_count: int
    estimated_tokens: int
    should_compact: bool


class CompactResponse(BaseModel):
    status: str
    thread_id: str
    message_count_after: int


class ThreadSummaryResponse(BaseModel):
    thread_id: str
    summary: str | None = None


# Task update model

class TaskUpdateRequest(BaseModel):
    message: str = Field(..., description="Follow-up message to send to the subagent")


# D2 — message injection (write to checkpoint without creating a run)

class InjectMessageRequest(BaseModel):
    content: str = Field(..., description="User message content to inject into the thread")
    agent_name: str | None = None  # which agent's graph to use for the checkpoint config


class AskUserAnswersRequest(BaseModel):
    status: str = "answered"   # "answered" | "cancelled"
    answers: list[str] = []


