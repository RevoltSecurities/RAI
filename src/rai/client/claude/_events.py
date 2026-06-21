"""Typed SSE event dataclasses for rai claude-serve streams.

parse_claude_event(d) dispatches a raw dict (from the JSON payload) to its typed
dataclass. All fields default to None/empty so partial server payloads never raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClaudeSessionIdEvent:
    session_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeTokenEvent:
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeThinkingEvent:
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeToolStartEvent:
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeToolResultEvent:
    id: str = ""
    output: str = ""
    is_error: bool = False
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeSubagentStartEvent:
    task_id: str = ""
    description: str = ""
    uuid: str = ""
    session_id: str = ""
    task_type: str = ""   # "local_bash" | "local_agent" | "remote_agent"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeSubagentProgressEvent:
    task_id: str = ""
    description: str = ""   # stdout line for Monitor watches
    last_tool: str = ""
    tokens: int = 0
    tool_uses: int = 0
    duration_ms: int = 0
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeSubagentDoneEvent:
    task_id: str = ""
    status: str = ""
    summary: str = ""
    output_file: str = ""   # path to full Monitor output log
    tokens: int = 0
    duration_ms: int = 0
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeHITLRequestEvent:
    tool_use_id: str = ""
    tool_name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    display_name: str = ""
    description: str = ""
    reason: str = ""
    agent_id: str | None = None
    session_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeHITLResponseEvent:
    tool_use_id: str = ""
    decision: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudePlanBlockedEvent:
    denied: list[Any] = field(default_factory=list)
    deferred: dict[str, Any] | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeRateLimitEvent:
    status: str = ""
    limit_type: str = ""
    resets_at: int = 0
    utilization: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeRunEndEvent:
    session_id: str = ""
    cost: float = 0.0
    turns: int = 0
    is_error: bool = False
    stop_reason: str = ""
    duration_ms: int = 0
    usage: dict[str, Any] | None = None
    errors: list[Any] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaudeErrorEvent:
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


_EVENT_MAP: dict[str, type] = {
    "session_id":       ClaudeSessionIdEvent,
    "token":            ClaudeTokenEvent,
    "thinking":         ClaudeThinkingEvent,
    "tool_start":       ClaudeToolStartEvent,
    "tool_result":      ClaudeToolResultEvent,
    "subagent_start":   ClaudeSubagentStartEvent,
    "subagent_progress": ClaudeSubagentProgressEvent,
    "subagent_done":    ClaudeSubagentDoneEvent,
    "hitl_request":     ClaudeHITLRequestEvent,
    "hitl_response":    ClaudeHITLResponseEvent,
    "plan_blocked":     ClaudePlanBlockedEvent,
    "rate_limit":       ClaudeRateLimitEvent,
    "run_end":          ClaudeRunEndEvent,
    "error":            ClaudeErrorEvent,
}


def parse_claude_event(d: dict[str, Any]) -> Any:
    """Return a typed event dataclass for d, or the raw dict if type is unknown."""
    cls = _EVENT_MAP.get(d.get("type", ""))
    if cls is None:
        return d
    fields = cls.__dataclass_fields__
    filtered = {k: v for k, v in d.items() if k in fields}
    filtered["data"] = d
    return cls(**filtered)
