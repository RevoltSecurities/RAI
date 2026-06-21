"""rai.sdk — full SDK for embedding and extending RAI as a library.

Submodules
----------
  rai.sdk.agent      — RunableAgent, RAIAgent
  rai.sdk.builder    — RAIAgentBuilder (fluent builder)
  rai.sdk.serve      — ServeConfig, serve_module (LangGraph API serving)
  rai.sdk.client     — RAIClient, all SSE event types, request/response types
  rai.sdk.tui        — RaiHttpTUI, RaiHttpTUIApp (Textual terminal UI)
  rai.sdk.engine     — create_rai_agent, run_agent, build_model, ModelConfig
  rai.sdk.harness    — RAIHTTPServer, HTTPConfig (RAI HTTP streaming server)
  rai.sdk.middleware — all middleware classes
  rai.sdk.tools      — all agent tools organized by domain
  rai.sdk.config     — settings, AgentConfig, load_agent_config
  rai.sdk.mcp        — load_mcp_tools, load_subagents_mcp_tools_map

Quick start::

    import asyncio
    from rai.sdk import RAIAgent

    async def main():
        async with RAIAgent.builder()
                .model("anthropic:claude-sonnet-4-6")
                .agent_name("pentest")
                .target("example.com")
                .build() as agent:
            result = await agent.run("Find OWASP Top 10 issues on the login endpoint")
            print(agent.thread_id)

    asyncio.run(main())
"""

from __future__ import annotations

# ---- High-level SDK --------------------------------------------------------
from rai.sdk.agent import RAIAgent, RunableAgent
from rai.sdk.builder import RAIAgentBuilder

# ---- Serve API -------------------------------------------------------------
from rai.sdk.serve import ServeConfig, ServeError, serve_module

# ---- HTTP harness ----------------------------------------------------------
from rai.sdk.harness import HTTPConfig, RAIHTTPServer

# ---- TUI -------------------------------------------------------------------
from rai.sdk.tui import RaiHttpTUI, RaiHttpTUIApp

# ---- HTTP client -----------------------------------------------------------
from rai.sdk.client import (
    RAIClient,
    ClientConfig,
    SSEEvent,
    PlanTimeoutError,
    # Event types — most commonly used
    RunStartEvent,
    RunEndEvent,
    TokenEvent,
    ThinkingEvent,
    ToolStartEvent,
    ToolEndEvent,
    InterruptEvent,
    InterruptResolvedEvent,
    PlanModeEnteredEvent,
    PlanReadyEvent,
    PlanApprovedEvent,
    PlanRejectedEvent,
    PlanCompletedEvent,
    StepStartEvent,
    StepCompleteEvent,
    StepBlockedEvent,
    SubagentStartedEvent,
    SubagentCompletedEvent,
    SubagentErrorEvent,
    TaskCreatedEvent,
    TaskStatusEvent,
    TaskCompletedEvent,
    ErrorEvent,
    NotificationEvent,
)

# ---- Claude Serve client ---------------------------------------------------
from rai.sdk.client import (
    ClaudeServeClient,
    ClaudeServeConfig,
    ClaudeSessionIdEvent,
    ClaudeTokenEvent,
    ClaudeThinkingEvent,
    ClaudeToolStartEvent,
    ClaudeToolResultEvent,
    ClaudeSubagentStartEvent,
    ClaudeSubagentProgressEvent,
    ClaudeSubagentDoneEvent,
    ClaudeHITLRequestEvent,
    ClaudeHITLResponseEvent,
    ClaudePlanBlockedEvent,
    ClaudeRateLimitEvent,
    ClaudeRunEndEvent,
    ClaudeErrorEvent,
    parse_claude_event,
)

# ---- Engine ----------------------------------------------------------------
from rai.sdk.engine import (
    create_rai_agent,
    get_system_prompt,
    DEFAULT_AGENT_NAME,
    DEFAULT_MODEL,
    build_model,
    ModelConfig,
    list_providers,
    run_agent,
)

# ---- Middleware -------------------------------------------------------------
from rai.sdk.middleware import (
    AuditLogMiddleware,
    ExecuteInterceptorMiddleware,
    FindingsEnrichmentMiddleware,
    HooksMiddleware,
    ModelCallLoggerMiddleware,
    ModelOverrideMiddleware,
    OPPLANMiddleware,
    RateLimitMiddleware,
    EmptyContentSanitizerMiddleware,
    RAIPromptCachingMiddleware,
    MemoryMiddleware,
    SkillsMiddleware,
    LocalAsyncAgentMiddleware,
    AsyncRaiSubagentsMiddleware,
    RTKToolMiddleware,
    MessageCompressionMiddleware,
    ToolResultCompressionMiddleware,
    WriteTruncateMiddleware,
    LoopDetectionMiddleware,
    ReadFileDedupMiddleware,
    SubagentRestrictMiddleware,
    AllowedToolsMiddleware,
    CostTrackingMiddleware,
    RetryMiddleware,
)

# ---- Subagent system v2 (builders/types) ------------------------------------
from rai.harness.subagents_v2 import (
    SubagentService,
    build_subagent_tools,
    build_subagent_system_section,
    AsyncTask,
    AsyncSubAgentState,
    CompletionNotification,
)

# ---- Tools (top-level getters) ---------------------------------------------
from rai.sdk.tools import (
    get_security_tools,
    get_builtin_tools,
    get_memory_tools,
    get_web_tools,
    get_cloud_tools,
    get_ad_tools,
    get_reversing_tools,
    get_android_tools,
    get_container_tools,
    get_opplan_tools,
    get_reference_tools,
    FindingsAddTool,
    FindingsExportTool,
    FindingsListTool,
    init_findings_store,
)

# ---- Config ----------------------------------------------------------------
from rai.sdk.config import settings, AgentConfig, load_agent_config

# ---- MCP -------------------------------------------------------------------
from rai.sdk.mcp import load_mcp_tools, load_subagents_mcp_tools_map

# ---- Session helpers -------------------------------------------------------
from rai.sessions.store import build_stream_config, generate_thread_id, get_checkpointer

# ---- Subagent / backend types (deepagents primitives) ----------------------
from deepagents import AsyncSubAgent, CompiledSubAgent, create_deep_agent
from deepagents.middleware.subagents import SubAgent
from deepagents.backends import CompositeBackend, LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend

# ---- Claude SDK (no API key — uses Claude Code subscription) ---------------
from rai.engine.claude_sdk import (
    ClaudeAgent,
    ClaudeAgentBuilder,
    ClaudeAgentRegistry,
    RunResult,
    RunnableClaudeAgent,
    build_claude_options,
    build_default_hooks,
    load_claude_subagents,
    ClaudeSessionManager,
    ForkSessionResult,
    SDKSessionInfo,
    SessionMessage,
)
# claude-agent-sdk primitive types — re-exported so users never import from claude_agent_sdk directly
from claude_agent_sdk.types import (
    AgentDefinition,
    HookMatcher,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
)
# Stream event types + helpers (all from events.py — single source of truth)
from rai.engine.claude_sdk.events import (
    AssistantMessage,
    UserMessage,
    SystemMessage,
    ResultMessage,
    StreamEvent,
    RateLimitEvent,
    HookEventMessage,
    MirrorErrorMessage,
    DeferredToolUse,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ServerToolUseBlock,
    ServerToolResultBlock,
    TaskStartedMessage,
    TaskProgressMessage,
    TaskNotificationMessage,
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
    STREAM_MESSAGE_START, STREAM_CONTENT_START, STREAM_CONTENT_DELTA,
    STREAM_CONTENT_STOP, STREAM_MESSAGE_DELTA, STREAM_MESSAGE_STOP,
    DELTA_TEXT, DELTA_THINKING, DELTA_SIGNATURE, DELTA_INPUT_JSON,
    BLOCK_TEXT, BLOCK_THINKING, BLOCK_TOOL_USE,
    STOP_END_TURN, STOP_TOOL_USE, STOP_MAX_TURNS, STOP_ERROR,
    TASK_COMPLETED, TASK_FAILED, TASK_STOPPED,
)

__all__ = [
    # Claude SDK (no API key)
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
    # claude-agent-sdk primitive types (re-exported)
    "AgentDefinition", "HookMatcher", "ClaudeAgentOptions",
    "PermissionResultAllow", "PermissionResultDeny",
    # Stream event / message types
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
    # Stream event type constants
    "STREAM_MESSAGE_START", "STREAM_CONTENT_START", "STREAM_CONTENT_DELTA",
    "STREAM_CONTENT_STOP", "STREAM_MESSAGE_DELTA", "STREAM_MESSAGE_STOP",
    "DELTA_TEXT", "DELTA_THINKING", "DELTA_SIGNATURE", "DELTA_INPUT_JSON",
    "BLOCK_TEXT", "BLOCK_THINKING", "BLOCK_TOOL_USE",
    "STOP_END_TURN", "STOP_TOOL_USE", "STOP_MAX_TURNS", "STOP_ERROR",
    "TASK_COMPLETED", "TASK_FAILED", "TASK_STOPPED",
    # High-level SDK
    "RAIAgent",
    "RAIAgentBuilder",
    "RunableAgent",
    # Serve API
    "ServeConfig",
    "ServeError",
    "serve_module",
    # Factory / engine
    "create_rai_agent",
    "create_deep_agent",
    "get_system_prompt",
    "DEFAULT_AGENT_NAME",
    "DEFAULT_MODEL",
    # Runner
    "run_agent",
    # Session
    "generate_thread_id",
    "get_checkpointer",
    "build_stream_config",
    # Subagent types
    "SubAgent",
    "AsyncSubAgent",
    "CompiledSubAgent",
    # Backends
    "CompositeBackend",
    "LocalShellBackend",
    "FilesystemBackend",
    # Middleware
    "MemoryMiddleware",
    "SkillsMiddleware",
    "AuditLogMiddleware",
    "ExecuteInterceptorMiddleware",
    "FindingsEnrichmentMiddleware",
    "HooksMiddleware",
    "ModelCallLoggerMiddleware",
    "ModelOverrideMiddleware",
    "OPPLANMiddleware",
    "RateLimitMiddleware",
    "EmptyContentSanitizerMiddleware",
    "LocalAsyncAgentMiddleware",
    "RAIPromptCachingMiddleware",
    "RTKToolMiddleware",
    "MessageCompressionMiddleware",
    "ToolResultCompressionMiddleware",
    "WriteTruncateMiddleware",
    "LoopDetectionMiddleware",
    "ReadFileDedupMiddleware",
    "SubagentRestrictMiddleware",
    "AllowedToolsMiddleware",
    "CostTrackingMiddleware",
    "RetryMiddleware",
    "AsyncRaiSubagentsMiddleware",
    # Subagent system v2
    "SubagentService",
    "build_subagent_tools",
    "build_subagent_system_section",
    "AsyncTask",
    "AsyncSubAgentState",
    "CompletionNotification",
    # Model SDK
    "build_model",
    "ModelConfig",
    "list_providers",
    # Tools
    "get_security_tools",
    "get_builtin_tools",
    "get_memory_tools",
    "get_web_tools",
    "get_cloud_tools",
    "get_ad_tools",
    "get_reversing_tools",
    "get_android_tools",
    "get_container_tools",
    "get_opplan_tools",
    "get_reference_tools",
    "FindingsAddTool",
    "FindingsExportTool",
    "FindingsListTool",
    "init_findings_store",
    # MCP
    "load_mcp_tools",
    "load_subagents_mcp_tools_map",
    # Config
    "settings",
    "AgentConfig",
    "load_agent_config",
    # HTTP Server
    "RAIHTTPServer",
    "HTTPConfig",
    # TUI
    "RaiHttpTUI",
    "RaiHttpTUIApp",
    # HTTP Client
    "RAIClient",
    "ClientConfig",
    "SSEEvent",
    "PlanTimeoutError",
    # Common event types
    "RunStartEvent",
    "RunEndEvent",
    "TokenEvent",
    "ThinkingEvent",
    "ToolStartEvent",
    "ToolEndEvent",
    "InterruptEvent",
    "InterruptResolvedEvent",
    "PlanModeEnteredEvent",
    "PlanReadyEvent",
    "PlanApprovedEvent",
    "PlanRejectedEvent",
    "PlanCompletedEvent",
    "StepStartEvent",
    "StepCompleteEvent",
    "StepBlockedEvent",
    "SubagentStartedEvent",
    "SubagentCompletedEvent",
    "SubagentErrorEvent",
    "TaskCreatedEvent",
    "TaskStatusEvent",
    "TaskCompletedEvent",
    "ErrorEvent",
    "NotificationEvent",
    # Claude Serve client
    "ClaudeServeClient",
    "ClaudeServeConfig",
    "ClaudeSessionIdEvent",
    "ClaudeTokenEvent",
    "ClaudeThinkingEvent",
    "ClaudeToolStartEvent",
    "ClaudeToolResultEvent",
    "ClaudeSubagentStartEvent",
    "ClaudeSubagentProgressEvent",
    "ClaudeSubagentDoneEvent",
    "ClaudeHITLRequestEvent",
    "ClaudeHITLResponseEvent",
    "ClaudePlanBlockedEvent",
    "ClaudeRateLimitEvent",
    "ClaudeRunEndEvent",
    "ClaudeErrorEvent",
    "parse_claude_event",
]
