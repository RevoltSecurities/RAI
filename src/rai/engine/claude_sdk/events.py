"""Typed event helpers for iterating over RunnableClaudeAgent.stream().

All message types come from claude_agent_sdk and are re-exported here so
callers import from rai.sdk only.

Usage pattern::

    from rai.sdk import (
        ClaudeAgent,
        # message types
        AssistantMessage, ResultMessage, StreamEvent,
        TaskStartedMessage, TaskProgressMessage, TaskNotificationMessage,
        RateLimitEvent, UserMessage,
        # content block types
        TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock,
        # helpers
        iter_tokens, iter_thinking, iter_tool_calls,
        is_token_event, is_thinking_event, is_tool_input_event,
    )

    async with ClaudeAgent.builder().agent_name("rai").without_hitl().build() as agent:
        async for msg in agent.stream("scan example.com"):
            for token in iter_tokens(msg):
                print(token, end="", flush=True)

            for chunk in iter_thinking(msg):
                pass  # handle thinking text

            for call in iter_tool_calls(msg):
                print(f"[tool] {call['name']}: {call['input']}")

            if isinstance(msg, TaskStartedMessage):
                print(f"[subagent] {msg.description}")

            if isinstance(msg, TaskNotificationMessage):
                print(f"[subagent done] {msg.status}: {msg.summary}")

            if isinstance(msg, ResultMessage):
                print(f"cost=${msg.total_cost_usd:.4f}, turns={msg.num_turns}")
                break
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

# Re-export all message and content-block types
from claude_agent_sdk.types import (
    AssistantMessage,
    HookEventMessage,
    MirrorErrorMessage,
    RateLimitEvent,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    DeferredToolUse,
)

if TYPE_CHECKING:
    from claude_agent_sdk.types import Message


# ---------------------------------------------------------------------------
# StreamEvent helpers — extract specific delta types from partial messages
# ---------------------------------------------------------------------------


def is_token_event(msg: Any) -> bool:
    """True when msg is a StreamEvent carrying a text_delta."""
    if not isinstance(msg, StreamEvent):
        return False
    ev = msg.event
    return (
        ev.get("type") == "content_block_delta"
        and ev.get("delta", {}).get("type") == "text_delta"
    )


def is_thinking_event(msg: Any) -> bool:
    """True when msg is a StreamEvent carrying a thinking_delta."""
    if not isinstance(msg, StreamEvent):
        return False
    ev = msg.event
    return (
        ev.get("type") == "content_block_delta"
        and ev.get("delta", {}).get("type") == "thinking_delta"
    )


def is_tool_input_event(msg: Any) -> bool:
    """True when msg is a StreamEvent carrying an input_json_delta (tool input building up)."""
    if not isinstance(msg, StreamEvent):
        return False
    ev = msg.event
    return (
        ev.get("type") == "content_block_delta"
        and ev.get("delta", {}).get("type") == "input_json_delta"
    )


def iter_tokens(msg: Any) -> Iterator[str]:
    """Yield text token(s) from a StreamEvent.

    Yields nothing for any other message type — safe to call on every message.

    Example::

        async for msg in agent.stream(prompt):
            for token in iter_tokens(msg):
                print(token, end="", flush=True)
    """
    if is_token_event(msg):
        yield msg.event["delta"]["text"]


def iter_thinking(msg: Any) -> Iterator[str]:
    """Yield thinking chunk(s) from a StreamEvent.

    Example::

        async for msg in agent.stream(prompt):
            for chunk in iter_thinking(msg):
                thinking_buffer += chunk
    """
    if is_thinking_event(msg):
        yield msg.event["delta"]["thinking"]


def iter_tool_input(msg: Any) -> Iterator[str]:
    """Yield partial JSON string(s) for a tool input delta."""
    if is_tool_input_event(msg):
        yield msg.event["delta"]["partial_json"]


# ---------------------------------------------------------------------------
# AssistantMessage content helpers
# ---------------------------------------------------------------------------


def iter_text_blocks(msg: Any) -> Iterator[TextBlock]:
    """Yield all TextBlock objects from a completed AssistantMessage."""
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                yield block


def iter_thinking_blocks(msg: Any) -> Iterator[ThinkingBlock]:
    """Yield all ThinkingBlock objects from a completed AssistantMessage."""
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, ThinkingBlock):
                yield block


def iter_tool_calls(msg: Any) -> Iterator[dict[str, Any]]:
    """Yield tool call dicts from a completed AssistantMessage.

    Each dict has keys: ``id``, ``name``, ``input``.

    Example::

        async for msg in agent.stream(prompt):
            for call in iter_tool_calls(msg):
                print(f"[{call['name']}] {call['input']}")
    """
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                yield {"id": block.id, "name": block.name, "input": block.input}


def full_text(msg: Any) -> str:
    """Return the concatenated text of all TextBlock objects in an AssistantMessage.

    Returns empty string for any other message type.
    """
    return "".join(block.text for block in iter_text_blocks(msg))


# ---------------------------------------------------------------------------
# Stream event type constants — use with ev["type"] comparisons
# ---------------------------------------------------------------------------

STREAM_MESSAGE_START     = "message_start"
STREAM_CONTENT_START     = "content_block_start"
STREAM_CONTENT_DELTA     = "content_block_delta"
STREAM_CONTENT_STOP      = "content_block_stop"
STREAM_MESSAGE_DELTA     = "message_delta"
STREAM_MESSAGE_STOP      = "message_stop"

DELTA_TEXT               = "text_delta"
DELTA_THINKING           = "thinking_delta"
DELTA_SIGNATURE          = "signature_delta"
DELTA_INPUT_JSON         = "input_json_delta"

BLOCK_TEXT               = "text"
BLOCK_THINKING           = "thinking"
BLOCK_TOOL_USE           = "tool_use"

# Stop reason values on ResultMessage.stop_reason
STOP_END_TURN            = "end_turn"
STOP_TOOL_USE            = "tool_use"
STOP_MAX_TURNS           = "max_turns"
STOP_ERROR               = "error"

# TaskNotificationMessage.status values
TASK_COMPLETED           = "completed"
TASK_FAILED              = "failed"
TASK_STOPPED             = "stopped"


__all__ = [
    # Message types
    "AssistantMessage",
    "UserMessage",
    "SystemMessage",
    "ResultMessage",
    "StreamEvent",
    "RateLimitEvent",
    "HookEventMessage",
    "MirrorErrorMessage",
    "DeferredToolUse",
    # Content block types
    "TextBlock",
    "ThinkingBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ServerToolUseBlock",
    "ServerToolResultBlock",
    # Subagent messages
    "TaskStartedMessage",
    "TaskProgressMessage",
    "TaskNotificationMessage",
    # StreamEvent predicate helpers
    "is_token_event",
    "is_thinking_event",
    "is_tool_input_event",
    # Iterators
    "iter_tokens",
    "iter_thinking",
    "iter_tool_input",
    "iter_text_blocks",
    "iter_thinking_blocks",
    "iter_tool_calls",
    "full_text",
    # Constants
    "STREAM_MESSAGE_START",
    "STREAM_CONTENT_START",
    "STREAM_CONTENT_DELTA",
    "STREAM_CONTENT_STOP",
    "STREAM_MESSAGE_DELTA",
    "STREAM_MESSAGE_STOP",
    "DELTA_TEXT",
    "DELTA_THINKING",
    "DELTA_SIGNATURE",
    "DELTA_INPUT_JSON",
    "BLOCK_TEXT",
    "BLOCK_THINKING",
    "BLOCK_TOOL_USE",
    "STOP_END_TURN",
    "STOP_TOOL_USE",
    "STOP_MAX_TURNS",
    "STOP_ERROR",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "TASK_STOPPED",
]
