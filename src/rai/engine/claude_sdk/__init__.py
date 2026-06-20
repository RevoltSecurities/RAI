"""rai.engine.claude_sdk — high-level SDK wrapping claude-agent-sdk.

No Anthropic API key required.  Runs on the user's Claude Code subscription.

Quick start::

    import asyncio
    from rai.engine.claude_sdk import ClaudeAgent, ClaudeSessionManager

    async def main():
        async with ClaudeAgent.builder().agent_name("rai").without_hitl().build() as agent:
            result = await agent.run("list OWASP Top 10 vulnerabilities")
            print(f"session={result.session_id[:8]}, cost=${result.total_cost_usd:.4f}")

    asyncio.run(main())

    # List recent sessions + read history
    mgr = ClaudeSessionManager()
    sessions = mgr.list(limit=10)
    msgs = mgr.messages(sessions[0].session_id)
    history = mgr.full_history(sessions[0].session_id)
"""

from __future__ import annotations

from rai.engine.claude_sdk.agent import RunnableClaudeAgent, RunResult
from rai.engine.claude_sdk.builder import ClaudeAgentBuilder
from rai.engine.claude_sdk.events import (
    # message types
    AssistantMessage,
    DeferredToolUse,
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
    # helpers
    is_token_event,
    is_thinking_event,
    is_tool_input_event,
    iter_tokens,
    iter_thinking,
    iter_tool_input,
    iter_text_blocks,
    iter_thinking_blocks,
    iter_tool_calls,
    full_text,
    # constants
    STREAM_MESSAGE_START,
    STREAM_CONTENT_START,
    STREAM_CONTENT_DELTA,
    STREAM_CONTENT_STOP,
    STREAM_MESSAGE_DELTA,
    STREAM_MESSAGE_STOP,
    DELTA_TEXT,
    DELTA_THINKING,
    DELTA_SIGNATURE,
    DELTA_INPUT_JSON,
    BLOCK_TEXT,
    BLOCK_THINKING,
    BLOCK_TOOL_USE,
    STOP_END_TURN,
    STOP_TOOL_USE,
    STOP_MAX_TURNS,
    STOP_ERROR,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_STOPPED,
)
from rai.engine.claude_sdk.hooks import build_default_hooks
from rai.engine.claude_sdk.options import build_claude_options
from rai.engine.claude_sdk.registry import ClaudeAgentRegistry
from rai.engine.claude_sdk.session import (
    ClaudeSessionManager,
    ForkSessionResult,
    SDKSessionInfo,
    SessionMessage,
)
from rai.engine.claude_sdk.subagents import load_claude_subagents


class ClaudeAgent:
    """Entry point — mirrors RAIAgent pattern.

    ClaudeAgent.builder() → ClaudeAgentBuilder  (single agent)
    ClaudeAgent.registry() → ClaudeAgentRegistry  (multi-agent)
    """

    @staticmethod
    def builder() -> ClaudeAgentBuilder:
        return ClaudeAgentBuilder()

    @staticmethod
    def registry() -> ClaudeAgentRegistry:
        return ClaudeAgentRegistry()


__all__ = [
    "ClaudeAgent",
    "ClaudeAgentBuilder",
    "RunnableClaudeAgent",
    "RunResult",
    "ClaudeAgentRegistry",
    "build_claude_options",
    "load_claude_subagents",
    "build_default_hooks",
    # Session management
    "ClaudeSessionManager",
    "ForkSessionResult",
    "SDKSessionInfo",
    "SessionMessage",
    # Event / message types
    "AssistantMessage", "UserMessage", "SystemMessage", "ResultMessage",
    "StreamEvent", "RateLimitEvent", "HookEventMessage", "MirrorErrorMessage",
    "DeferredToolUse",
    "TextBlock", "ThinkingBlock", "ToolUseBlock", "ToolResultBlock",
    "ServerToolUseBlock", "ServerToolResultBlock",
    "TaskStartedMessage", "TaskProgressMessage", "TaskNotificationMessage",
    # Event helpers
    "is_token_event", "is_thinking_event", "is_tool_input_event",
    "iter_tokens", "iter_thinking", "iter_tool_input",
    "iter_text_blocks", "iter_thinking_blocks", "iter_tool_calls",
    "full_text",
    # Constants
    "STREAM_MESSAGE_START", "STREAM_CONTENT_START", "STREAM_CONTENT_DELTA",
    "STREAM_CONTENT_STOP", "STREAM_MESSAGE_DELTA", "STREAM_MESSAGE_STOP",
    "DELTA_TEXT", "DELTA_THINKING", "DELTA_SIGNATURE", "DELTA_INPUT_JSON",
    "BLOCK_TEXT", "BLOCK_THINKING", "BLOCK_TOOL_USE",
    "STOP_END_TURN", "STOP_TOOL_USE", "STOP_MAX_TURNS", "STOP_ERROR",
    "TASK_COMPLETED", "TASK_FAILED", "TASK_STOPPED",
]
